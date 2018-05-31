import re
import io
import hashlib
import discord
import aiohttp
import asyncio
import time
import datetime
from PIL import Image
from discord.ext import commands
from discord.ext.commands import BucketType
from utils.language import getlang

import utils.sqlite as sql
import utils.canvases as canvases
from utils import checks
from utils import utils
from utils import colors
from utils import render
from utils.logger import Log
from utils.config import Config
from objects.template import Template as T2

log = Log(__name__)
cfg = Config()

# TODO:
# - add "check all" feature
# - add faction support
# - add cross-guild template sharing
# - add help for new commands
# - add command logging
# - write database update sql
# - localize new strings
# - extract duplicate code and refactor the ugly shit
# - housekeeping
# - update wiki


class Template:
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name='templates', invoke_without_command=True, aliases=['t'])
    @commands.cooldown(1, 5, BucketType.guild)
    async def templates(self, ctx, page: int=1):
        ts = sql.get_templates_by_guild(ctx.guild.id)
        if len(ts) > 0:
            pages = 1 + len(ts) // 10
            page = min(max(page, 1), pages)
            w1 = max(max(map(lambda tx: len(tx.name), ts)) + 2, len("Name"))
            out = "**Template List** - Page {0}/{1}\n```xl\n".format(page, pages)  # TODO: Localize string
            out = out + "{0:<{w1}}  {1:<14}  {2}\n".format("Name", "Canvas", "Coords", w1=w1)  # TODO: Localize string
            for t in ts[(page-1)*10:page*10]:
                coords = "({}, {})".format(t.x, t.y)
                out = out + "{0:<{w1}}  {1:<14}  {2}\n".format('"' + t.name + '"', canvases.pretty_print[t.canvas], coords, w1=w1)
            out = out + "\n// Use '{0}templates <page>' to see that page\n// Use '{0}templates info <name>' to see more info on a template```".format(sql.get_guild_prefix(ctx.guild.id))  # TODO: Localize string
            await ctx.send(out)
        else:
            await ctx.send("This guild currently has no templates.")  # TODO: Localize string

    @templates.group(name='add', invoke_without_command=True)
    @commands.cooldown(1, 5, BucketType.guild)
    @checks.template_adder_only()
    async def templates_add(self, ctx):
        await canvases.invoke_default(ctx, self.bot, "templates.add")

    @templates_add.command(name="pixelcanvas", aliases=['pc'])
    @commands.cooldown(1, 5, BucketType.guild)
    @checks.template_adder_only()
    async def templates_add_pixelcanvas(self, ctx, name, x, y, url=None):
        await self.add_template(ctx, "pixelcanvas", name, x, y, url)

    @templates_add.command(name="pixelzio", aliases=['pzi'])
    @commands.cooldown(1, 5, BucketType.guild)
    @checks.template_adder_only()
    async def templates_add_pixelzio(self, ctx, name, x, y, url=None):
        await self.add_template(ctx, "pixelzio", name, x, y, url)

    @templates_add.command(name="pixelzone", aliases=['pz'])
    @commands.cooldown(1, 5, BucketType.guild)
    @checks.template_adder_only()
    async def templates_add_pixelzone(self, ctx, name, x, y, url=None):
        await self.add_template(ctx, "pixelzone", name, x, y, url)

    @templates_add.command(name="pxlsspace", aliases=['ps'])
    @commands.cooldown(1, 5, BucketType.guild)
    @checks.template_adder_only()
    async def templates_add_pxlsspace(self, ctx, name, x, y, url=None):
        await self.add_template(ctx, "pxlsspace", name, x, y, url)

    @templates.command(name='remove', aliases=['rm'])
    @commands.cooldown(1, 5, BucketType.guild)
    @checks.template_adder_only()
    async def templates_remove(self, ctx, name):
        t = sql.get_template_by_name(ctx.guild.id, name)
        if not t:
            await ctx.send("There is no template named '{0}'.".format(name))  # TODO: Localize string
            return
        if t.owner_id != ctx.author.id and not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            await ctx.send("You do not have permission to modify a template you did not add.")  # TODO: Localize string
            return
        sql.drop_template(t.gid, t.name)
        await ctx.send("Successfully removed '{0}'.".format(name))  # TODO: Localize string

    @templates.command(name='info')
    @commands.cooldown(1, 5, BucketType.guild)
    async def templates_info(self, ctx, name):
        t = sql.get_template_by_name(ctx.guild.id, name)
        if not t:
            await ctx.send("Could not find template with name `{0}`.".format(name))  # TODO: Localize string
            return

        canvas_url = canvases.url_templates[t.canvas].format(*t.center())
        owner = self.bot.get_user(t.owner_id)
        time_added = datetime.date.fromtimestamp(t.date_created)
        time_modified = datetime.date.fromtimestamp(t.date_updated)
        e = discord.Embed(title=t.name, url=canvas_url, color=13594340)\
            .set_image(url=t.url)\
            .add_field(name="Canvas", value=canvases.pretty_print[t.canvas], inline=True)\
            .add_field(name="Location", value="({0}, {1})".format(t.x, t.y), inline=True)\
            .add_field(name="Size", value="{0}x{1}px".format(t.width, t.height), inline=True)\
            .add_field(name="Added By", value=owner.name + "#" + owner.discriminator, inline=True)\
            .add_field(name="Date Added", value=time_added.strftime("%d %b, %Y"), inline=True)\
            .add_field(name="Date Modified", value=time_modified.strftime("%d %b, %Y"), inline=True)
        await ctx.send(embed=e)

    @staticmethod
    async def select_url(ctx, input_url=None):
        if len(ctx.message.attachments) > 0:
            return ctx.message.attachments[0].url
        if input_url is not None:
            if re.search('^(?:https?://)cdn\.discordapp\.com/', input_url) is not None:
                return input_url
            await ctx.send("I can only accept Discord attachment URLs.")  # TODO: localize string

    async def build_template(self, ctx, name, x, y, input_url, canvas):
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(input_url) as resp:
                    if resp.status != 200:
                        print("Response not OK")  # TODO: Add output and localize string
                        return
                    if resp.content_type == "image/jpg" or resp.content_type == "image/jpeg":
                        try:
                            f = discord.File("assets/disdain_for_jpegs.gif", "disdain_for_jpegs.gif")
                            await ctx.send(getlang(ctx.guild.id, "bot.error.jpeg"), file=f)
                        except IOError:
                            await ctx.send(getlang(ctx.guild.id, "bot.error.jpeg"))
                        return
                    if resp.content_type != "image/png":
                        await ctx.send(getlang(ctx.guild.id, "bot.error.no_png"))
                        return
                    data = await resp.read()
                    with io.BytesIO(data) as bio:
                        md5 = hashlib.md5(bio.getvalue()).hexdigest()
                        with Image.open(bio) as tmp:
                            w, h = tmp.size
                            quantized = await Template.check_colors(tmp, colors.by_name[canvas])
                        if not quantized:
                            q = await ctx.send("This image contains colors that are not part of this canvas's palette. Would you like to quantize it?\n  `0` - No\n  `1` - Yes")  # TODO: Localize string
                            if not await Template.wait_for_confirm(self, ctx, q):
                                return
                            new_msg = await render.quantize(ctx, bio, colors.by_name[canvas])
                            input_url = new_msg.attachments[0].url
                            async with aiohttp.ClientSession() as sess2:
                                async with sess2.get(input_url) as resp2:
                                    if resp2.status != 200:
                                        print("Response not OK")  # TODO: Add output and localize string
                                        return
                                    data = await resp2.read()
                                    with io.BytesIO(data) as bio2:
                                        md5 = hashlib.md5(bio2.getvalue()).hexdigest()

                        created = int(time.time())
                        return T2(ctx.guild.id, name, input_url, canvas, x, y, w, h, created, created, md5, ctx.author.id)
        except aiohttp.client_exceptions.InvalidURL:
            print("Not a URL.")  # TODO: Add output and localize string
        except IOError:
            await ctx.send(getlang(ctx.guild.id, "bot.error.bad_png")
                           .format(sql.get_guild_prefix(ctx.guild.id), getlang(ctx.guild.id, "command.quantize")))

    @staticmethod
    async def check_colors(img, palette):
        for py in range(img.height):
            await asyncio.sleep(0)
            for px in range(img.width):
                pix = img.getpixel((px, py))
                if pix[3] == 0:  # Ignore fully transparent pixels
                    continue
                if pix[3] != 255:  # Break on semi-transparent
                    return False
                if pix[:3] not in palette:
                    return False
        return True

    async def wait_for_confirm(self, ctx, query_msg):
        sql.add_menu_lock(ctx.channel.id, ctx.author.id)

        def check(m):
            return ctx.channel.id == m.channel.id and ctx.author.id == m.author.id

        try:
            resp_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            while not (resp_msg.content == "0" or resp_msg.content == "1"):
                await ctx.send("That is not a valid option. Please try again.")  # TODO: localize string
                resp_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
        except asyncio.TimeoutError:
            await query_msg.edit(content="Command timed out.")  # TODO: localize string
            return False
        finally:
            sql.remove_menu_lock(ctx.channel.id, ctx.author.id)
        return resp_msg.content == "1"

    async def add_template(self, ctx, canvas, name, x, y, url):
        if len(name) > cfg.max_template_name_length:
            await ctx.send("That name is too long. Please use a name under {0} characters.".format(cfg.max_template_name_length))  # TODO: Localize string
            return
        ct = sql.count_templates(ctx.guild.id)
        if ct >= cfg.max_templates_per_guild:
            await ctx.send("This guild already has the maximum number of templates. Please remove a template before adding another.")  # TODO: Localize string
            return
        url = await self.select_url(ctx, url)
        if url is None:
            return
        t = await self.build_template(ctx, name, x, y, url, canvas)
        if not t:
            return

        t_by_name = sql.get_template_by_name(ctx.guild.id, t.name)
        ts_by_mdd5 = sql.get_templates_by_hash(ctx.guild.id, t.md5)
        if t_by_name:
            if t.owner_id != ctx.author.id and not utils.is_admin(ctx):
                await ctx.send("A template with that name already exists. You do not have permission to modify a template you did not add.")  # TODO: Localize string
                return
            query_msg = await ctx.send(
                "A template with the name '{0}' already exists for {1} at ({2}, {3}). Replace it?\n  `0` - No\n  `1` - Yes".format(
                    t_by_name.name, canvases.pretty_print[t_by_name.canvas], t_by_name.x, t_by_name.y))  # TODO: localize string
            if not await self.wait_for_confirm(ctx, query_msg):
                return
            if ts_by_mdd5:
                ts_by_mdd5 = [z for z in ts_by_mdd5 if z.name != t.name]
                if len(ts_by_mdd5) > 0:
                    m = "The following templates already match this image:\n```xl\n"
                    maxlength = max(map(lambda c: len(t_by_name.name), ts_by_mdd5))
                    for t_by_name in ts_by_mdd5:
                        m = m + "'{0:<{width}}' {1:>15} ({2}, {3})\n".format(t_by_name.name, canvases.pretty_print[t_by_name.canvas], t_by_name.x, t_by_name.y,
                                                                             width=maxlength)
                    m = m + "```\nCreate a new template anyway?\n  `0` - No\n  `1` - Yes"  # TODO: localize string
                    query_msg = await ctx.send(m)
                    if not await self.wait_for_confirm(ctx, query_msg):
                        return
            # TODO: If mod, update owner
            sql.update_template(t)
            await ctx.send("Template '{0}' updated!".format(name))  # TODO: localize string
            return
        elif len(ts_by_mdd5) > 0:
            m = "The following templates already match this image:\n```xl\n"
            maxlength = max(map(lambda tx: len(tx.name), ts_by_mdd5)) + 2
            for t_by_name in ts_by_mdd5:
                m = m + "{0:<{width}} {1:>15} ({2}, {3})\n".format("'" + t_by_name.name + "'", canvases.pretty_print[t_by_name.canvas], t_by_name.x, t_by_name.y,
                                                                   width=maxlength)
            m = m + "```\nCreate a new template anyway?\n  `0` - No\n  `1` - Yes"  # TODO: localize string
            query_msg = await ctx.send(m)
            if not await self.wait_for_confirm(ctx, query_msg):
                return
        sql.add_template(t)
        await ctx.send("Template '{0}' added!".format(name))  # TODO: localize string


def setup(bot):
    bot.add_cog(Template(bot))
