from .ventilationcontrol import VentilationControl

async def setup(bot):
    await bot.add_cog(ventcontrol(bot))