import discord
from discord.ext import commands
import os

# --- Bot Setup ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- Bot Events ---
@bot.event
async def on_ready():
    """
    This event fires when the bot successfully connects to Discord.
    It loads all cogs and then syncs the slash commands.
    """
    print(f'Logged in as {bot.user.name}')
    print('Bot is ready!')

    try:
        # Load all the cogs from the 'cogs' directory.
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                cog_name = f'cogs.{filename[:-3]}'
                try:
                    await bot.load_extension(cog_name)
                    print(f"Loaded extension: {cog_name}")
                except Exception as e:
                    print(f'Failed to load extension {cog_name}: {e}')

        # Sync slash commands with Discord after all cogs have been loaded.
        synced_commands = await bot.tree.sync()
        print(f"Synced {len(synced_commands)} global slash command(s).")
    except Exception as e:
        print(f"An error occurred during bot setup or cog loading: {e}")

# --- Run the Bot ---
TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
if TOKEN is None:
    print("Error: DISCORD_BOT_TOKEN environment variable is not set.")
    print("Please set the 'DISCORD_BOT_TOKEN' environment variable with your bot token.")
else:
    bot.run(TOKEN)