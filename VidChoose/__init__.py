from .vidchoose import VidChoose

async def setup(bot):
    await bot.add_cog(VidChoose(bot))
