import discord
from discord.ext import commands
import asyncio
import os

# --- Configuration (using environment variables) ---
# NOTE: TOKEN and INTRO_CHANNEL_ID are now handled by main.py
# and the bot instance, so these lines can be removed.
# INTRO_CHANNEL_ID is a parameter you can pass to the cog, or keep as an env var.
INTRO_CHANNEL_ID = int(os.environ.get('INTRO_CHANNEL_ID', '0'))

# --- Data Storage ---
# These can be properties of the class
introduction_responses = {}
temp_channels = {}
temp_channel_timeouts = {}

# --- Timezone Options (categorized for multiple dropdowns) ---
TIMEZONE_OPTIONS_BY_REGION = {
    "North America": [
        "EST (GMT -5:00)", "CST (GMT -6:00)", "MST (GMT -7:00)", "PST (GMT -8:00)",
        "AKST (GMT -9:00)", "HST (GMT -10:00)", "AST (GMT -4:00)", "NST (GMT -3:30)"
    ],
    "Europe": [
        "GMT (GMT)", "CET (GMT +1:00)", "EET (GMT +2:00)", "MSK (GMT +3:00)",
        "WEST (GMT +1:00)", "IST (GMT +1:00)"
    ],
    "Asia": [
        "IST (GMT +5:30)", "CST (GMT +8:00)", "JST (GMT +9:00)", "ACT (GMT +9:30)",
        "AET (GMT +10:00)", "SST (GMT +11:00)", "NZST (GMT +12:00)"
    ],
    "Other/General": [
        "UTC", "ART (GMT -3:00)", "WET (GMT +0:00)", "EAT (GMT +3:00)",
        "ACST (GMT +9:30)", "AWST (GMT +8:00)"
    ]
}

# --- Custom UI Views for Multi-Level Selection ---
# These classes can remain outside the cog class or be nested within it.
# They will need to be updated to reference the bot instance correctly.
class TimezoneCategorySelect(discord.ui.Select):
    """First dropdown for selecting a timezone region."""
    def __init__(self, parent_view):
        self.parent_view = parent_view
        options = [discord.SelectOption(label=region, value=region) for region in TIMEZONE_OPTIONS_BY_REGION.keys()]
        super().__init__(placeholder="Choose a region...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_region = self.values[0]
        self.parent_view.clear_items()
        self.parent_view.add_item(TimezoneDetailSelect(self.parent_view, self.parent_view.user_id, selected_region))
        await interaction.response.edit_message(content=f"You selected region: {selected_region}. Now choose your specific timezone.", view=self.parent_view)

class TimezoneDetailSelect(discord.ui.Select):
    """Second dropdown for selecting a specific timezone within a chosen region."""
    def __init__(self, parent_view, user_id, selected_region):
        self.parent_view = parent_view
        self.user_id = user_id
        options = [
            discord.SelectOption(label=tz, value=tz)
            for tz in TIMEZONE_OPTIONS_BY_REGION.get(selected_region, [])
        ]
        super().__init__(placeholder="Choose your timezone...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        introduction_responses[self.user_id]["Timezone"] = self.values[0]
        await interaction.response.send_message(f"You selected: {self.values[0]}", ephemeral=True)
        self.parent_view.stop()

class MultiSelectView(discord.ui.View):
    """A view that manages both the region and detail dropdowns."""
    def __init__(self, user_id):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.add_item(TimezoneCategorySelect(self))

    async def on_timeout(self):
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)
        self.stop()

# --- Main Cog Class ---
class IntroductionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Re-initialize data storage for the cog instance
        self.introduction_responses = {}
        self.temp_channels = {}
        self.temp_channel_timeouts = {}

    # Helper function for cleanup (now a method of the class)
    async def cleanup_introduction(self, user_id):
        # Use self.introduction_responses etc.
        if user_id in self.introduction_responses:
            del self.introduction_responses[user_id]
        if user_id in self.temp_channel_timeouts:
            self.temp_channel_timeouts[user_id].cancel()
            del self.temp_channel_timeouts[user_id]
        if user_id in self.temp_channels:
            temp_channel = self.temp_channels[user_id]
            try:
                if self.bot.get_channel(temp_channel.id):
                    await temp_channel.delete(reason="Introduction process completed or cancelled")
            except discord.Forbidden:
                print(f"Warning: Bot lacks permission to delete temporary channel for user {user_id} ({temp_channel.name}).")
                user = self.bot.get_user(user_id)
                if user:
                    try:
                        await user.send(f"I've completed your introduction but couldn't delete your temporary channel ({temp_channel.mention}). Please delete it manually if you're done.")
                    except discord.Forbidden:
                        print(f"Warning: Could not send DM to user {user_id} about channel deletion failure.")
            except Exception as e:
                print(f"Error deleting temporary channel for user {user_id}: {e}")
                user = self.bot.get_user(user_id)
                if user:
                    try:
                        await user.send(f"An error occurred while deleting your temporary channel ({temp_channel.mention}): {e}. Please delete it manually if you're done.")
                    except discord.Forbidden:
                        print(f"Warning: Could not send DM to user {user_id} about channel deletion failure.")
            finally:
                del self.temp_channels[user_id]

    # --- Bot Slash Commands (now a method of the class) ---
    @commands.hybrid_command(name="introductions", description="Start the introduction process to introduce yourself in a private channel.")
    async def introductions_slash(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild

        # ... (The rest of your command logic remains largely the same,
        # but you must use `self.` to reference the bot instance and class properties)

        if not guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        user_id = user.id

        if user_id in self.introduction_responses:
            await interaction.response.send_message("You are already completing an introduction. Please finish or wait for the process to timeout.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        channel_name = f"intro-{user.name.lower().replace(' ', '-')}"
        channel_name = "".join(c for c in channel_name if c.isalnum() or c == '-').lower()

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        try:
            temp_channel = await guild.create_text_channel(
                channel_name,
                overwrites=overwrites,
                reason=f"Introduction channel for {user.display_name}"
            )
            self.temp_channels[user_id] = temp_channel
            self.introduction_responses[user_id] = {}

            await interaction.followup.send(
                f"I've created a private channel for your introduction: {temp_channel.mention}. "
                "Please go there to answer the questions. You can type 'quit' or 'restart' at any time.",
                ephemeral=True
            )
            await temp_channel.send(f"Hello {user.mention}! Please answer the following questions to introduce yourself.")

        except discord.Forbidden:
            await interaction.followup.send("I don't have permissions to create channels. Please check my role permissions.", ephemeral=True)
            await self.cleanup_introduction(user_id)
            return
        except Exception as e:
            await interaction.followup.send(f"An error occurred while creating the channel: {e}", ephemeral=True)
            await self.cleanup_introduction(user_id)
            return

        async def channel_timeout():
            await asyncio.sleep(600)
            if user_id in self.temp_channels:
                await self.temp_channels[user_id].send(f"{user.mention}, you took too long to complete your introduction. The process has been cancelled.")
                await self.cleanup_introduction(user_id)

        timeout_task = self.bot.loop.create_task(channel_timeout())
        self.temp_channel_timeouts[user_id] = timeout_task

        questions = [
            "What is your real name? (Optional)",
            "Where are you from/living?",
            "What server/alliance are you joining us from?",
            "What is your native language?",
            "Do you speak any others?",
            "Do you have any pets?",
            "What's your favorite hobby outside of gaming?",
            "Favorite movie or TV show?",
            "What's your hidden talent"
        ]

        for question in questions:
            await temp_channel.send(question)
            try:
                message = await self.bot.wait_for(
                    'message',
                    check=lambda m: m.author == user and m.channel == temp_channel,
                    timeout=60.0
                )
                response_text = message.content.strip().lower()

                if response_text == 'quit':
                    await temp_channel.send("Introduction process cancelled.")
                    await self.cleanup_introduction(user_id)
                    return
                elif response_text == 'restart':
                    await temp_channel.send("Restarting introduction process...")
                    await self.cleanup_introduction(user_id)
                    await self.introductions_slash(interaction)
                    return

                self.introduction_responses[user_id][question] = message.content
            except asyncio.TimeoutError:
                await temp_channel.send(f"{user.mention}, you took too long to respond to the last question. The process has been cancelled.")
                await self.cleanup_introduction(user_id)
                return
            except asyncio.CancelledError:
                return

        # --- Timezone Question (Multi-Level Dropdowns) ---
        await temp_channel.send("Please select your timezone:")
        view = MultiSelectView(user_id)
        await temp_channel.send("First, choose a region:", view=view)

        try:
            await view.wait()
            if "Timezone" not in self.introduction_responses[user_id]:
                await temp_channel.send(f"{user.mention}, you took too long to select your timezone. The process has been cancelled.")
                await self.cleanup_introduction(user_id)
                return
        except asyncio.TimeoutError:
            await temp_channel.send(f"{user.mention}, you took too long to select your timezone. The process has been cancelled.")
            await self.cleanup_introduction(user_id)
            return
        except asyncio.CancelledError:
            return
        finally:
            if user_id in self.temp_channel_timeouts:
                self.temp_channel_timeouts[user_id].cancel()
                del self.temp_channel_timeouts[user_id]

    # --- Compile and Post Introduction (CLEANER LOOK) ---
        embed = discord.Embed(
            title=f"New Introduction from {user.display_name}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=user.avatar.url)
        embed.set_footer(text=f"Introduction completed on {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        for question, answer in self.introduction_responses[user_id].items():
            cleaned_question = question.replace('(Optional)', '').strip()
            embed.add_field(name=f"**{cleaned_question}**", value=answer, inline=False)

        target_channel = self.bot.get_channel(INTRO_CHANNEL_ID)
        if target_channel:
            try:
                await target_channel.send(embed=embed) # Send the embed instead of the string
                await temp_channel.send("Your introduction has been posted to the introductions channel!")
            except discord.Forbidden:
                await temp_channel.send(f"Error: I don't have permissions to post in the designated introduction channel ({target_channel.mention}). Please check my permissions in that channel.", ephemeral=False)
                print(f"Error: Bot lacks permissions to post in channel ID {INTRO_CHANNEL_ID}")
            except Exception as e:
                await temp_channel.send(f"An unexpected error occurred while posting your introduction: {e}", ephemeral=False)
                print(f"Error posting introduction for {user.id}: {e}")
        else:
            await temp_channel.send("Error: Could not find the introduction channel. Please contact an admin to ensure `INTRO_CHANNEL_ID` is correct.", ephemeral=False)

        await self.cleanup_introduction(user_id)

# --- This is the new, required setup function ---
# It takes the bot instance and adds your cog to it.
async def setup(bot):
    await bot.add_cog(IntroductionsCog(bot))
