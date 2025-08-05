import discord
from discord.ext import commands
import os

# --- Bot Setup ---
# Define the bot's intents.
# discord.Intents.default() gives you a set of common permissions.
# We also need to explicitly enable `members` and `message_content` intents for the bot's functionality.
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# Create the bot instance.
# The command prefix is a fallback; your bot primarily uses slash commands.
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Bot Events ---
@bot.event
async def on_ready():
    """
    This event fires when the bot successfully connects to Discord.
    It's used to confirm the bot is online and to sync slash commands.
    """
    print(f'Logged in as {bot.user.name}')
    print('Bot is ready!')

    try:
        # Load all the cogs from the 'cogs' directory.
        # This is a common and efficient way to manage bot functionality.
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                # The cog file name is "introductions.py", so we load it as "cogs.introductions".
                await bot.load_extension(f'cogs.{filename[:-3]}')
                print(f"Loaded extension: cogs.{filename[:-3]}")

        # Sync slash commands with Discord.
        synced_commands = await bot.tree.sync()
        print(f"Synced {len(synced_commands)} slash command(s).")
    except Exception as e:
        print(f"An error occurred during bot setup or cog loading: {e}")

# --- Run the Bot ---
# The bot token is retrieved from an environment variable for security.
TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
if TOKEN is None:
    print("Error: DISCORD_BOT_TOKEN environment variable is not set.")
    print("Please set the 'DISCORD_BOT_TOKEN' environment variable with your bot token.")
else:
    bot.run(TOKEN)
