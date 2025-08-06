import discord
from discord.ext import commands
import os
import logging

# --- Logging Setup ---
# Configure logging to write to a file inside the container and to the console.
# The `docker-compose.yaml` file will map this file to the host machine.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s:%(levelname)s:%(name)s: %(message)s',
    handlers=[
        logging.FileHandler("/app/bot.log"),
        logging.StreamHandler()
    ]
)

# --- Bot Setup ---
# Define the bot's intents.
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# Create the bot instance.
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Bot Events ---
@bot.event
async def on_ready():
    """
    This event fires when the bot successfully connects to Discord.
    It's used to confirm the bot is online and to sync slash commands.
    """
    logging.info(f'Logged in as {bot.user.name}')
    logging.info('Bot is ready!')

    try:
        # Load all the cogs from the 'cogs' directory.
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                await bot.load_extension(f'cogs.{filename[:-3]}')
                logging.info(f"Loaded extension: cogs.{filename[:-3]}")

        # Sync slash commands with Discord.
        synced_commands = await bot.tree.sync()
        logging.info(f"Synced {len(synced_commands)} slash command(s).")
    except Exception as e:
        logging.error("An error occurred during bot setup or cog loading:", exc_info=True)

@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Manually syncs slash commands with Discord."""
    try:
        synced_commands = await bot.tree.sync()
        logging.info(f"Manual sync requested by {ctx.author}. Synced {len(synced_commands)} command(s).")
        await ctx.send(f"Synced {len(synced_commands)} slash command(s).")
    except Exception as e:
        logging.error("An error occurred during manual command syncing:", exc_info=True)
        await ctx.send(f"An error occurred during command syncing: {e}")

# --- Run the Bot ---
# The bot token is retrieved from an environment variable for security.
TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
if TOKEN is None:
    logging.critical("DISCORD_BOT_TOKEN environment variable is not set.")
else:
    bot.run(TOKEN)
