from discord.ext import commands
import discord
from discord import app_commands
import datetime
import asyncio
import math
from tabulate import tabulate

def is_admin():
    def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)

class Prediction:
    def __init__(self, question, end_time, options, creator_id, cog, category=None):
        self.question = question
        self.end_time = end_time
        self.options = options
        self.creator_id = creator_id
        self.cog = cog
        self.category = category
        self.bets = {option: {} for option in options}
        self.resolved = False
        self.result = None
        self.refunded = False
        self.total_bets = 0
        self.user_votes = set()
        self.votes = {option: set() for option in options}
        self.initial_liquidity = 30000
        self.liquidity_pool = {option: self.initial_liquidity for option in options}
        self.k_constant = self.initial_liquidity * self.initial_liquidity

        # Verify initialization
        assert self.cog is not None, "Cog instance is not initialized."
        assert self.cog.bot is not None, "Bot instance is not initialized."

    def get_price(self, option, shares_to_buy):
        if option not in self.liquidity_pool:
            return 0
        
        current_shares = self.liquidity_pool[option]
        other_shares = self.liquidity_pool[self.get_opposite_option(option)]
        new_shares = current_shares - shares_to_buy
        if new_shares <= 0:
            return float('inf')
        
        new_other_shares = self.k_constant / new_shares
        cost = new_other_shares - other_shares
        return max(0, cost)

    def get_opposite_option(self, option):
        return [opt for opt in self.options if opt != option][0]

    async def place_bet(self, user_id, option, amount):
        if option not in self.liquidity_pool:
            return False

        shares = self.calculate_shares_for_points(option, amount)
        if shares <= 0:
            return False

        self.liquidity_pool[option] -= shares
        opposite_option = self.get_opposite_option(option)
        self.liquidity_pool[opposite_option] += amount

        if option not in self.bets:
            self.bets[option] = {}
        
        if user_id not in self.bets[option]:
            self.bets[option][user_id] = {'amount': 0, 'shares': 0}
        
        self.bets[option][user_id]['amount'] += amount
        self.bets[option][user_id]['shares'] += shares

        self.total_bets += amount
        
        await self.cog.points_manager.remove_points(user_id, amount)
        return True

    def calculate_shares_for_points(self, option, points):
        current_shares = self.liquidity_pool[option]
        other_shares = self.liquidity_pool[self.get_opposite_option(option)]
        new_other_shares = other_shares + points
        new_shares = self.k_constant / new_other_shares
        shares_received = current_shares - new_shares
        return shares_received

    def get_odds(self):
        total_bets_per_option = {
            option: sum(user_bets['amount'] for user_bets in self.bets[option].values())
            for option in self.options
        }
        total_all_bets = sum(total_bets_per_option.values())
        
        if total_all_bets == 0:
            return {option: 1/len(self.options) for option in self.options}
        
        return {
            option: total_bets_per_option[option] / total_all_bets
            for option in self.options
        }

    def get_user_payout(self, user_id):
        if not self.resolved or self.result is None:
            return 0
        
        user_bet_info = self.bets[self.result].get(user_id, None)
        if user_bet_info is None:
            print(f"User ID: {user_id} has no shares in the winning option.")
            return 0

        shares = user_bet_info['shares']
        if shares == 0:
            print(f"User ID: {user_id} has no shares in the winning option.")
            return 0

        total_pool = sum(sum(user_bets['amount'] for user_bets in option_bets.values()) for option_bets in self.bets.values())
        total_winning_bets = sum(user_bet_info['amount'] for user_bet_info in self.bets[self.result].values())
        
        print(f"Total Pool: {total_pool}, Total Winning Bets: {total_winning_bets}, User Shares: {shares}")

        if total_winning_bets <= 0:
            print(f"No winning bets found for User ID: {user_id}. Total Winning Bets is zero.")
            return 0

        share_value = total_pool / total_winning_bets
        payout = int(shares * share_value)
        
        print(f"User ID: {user_id}, Shares: {shares}, Share Value: {share_value}, Payout: {payout}")
        
        return payout

    async def async_resolve(self, winning_option):
        self.resolved = True
        self.result = winning_option
        winning_users = self.bets[self.result].items()
        print(f"Winning option: {self.result}")
        print(f"Winning users: {list(winning_users)}")

        if not winning_users:
            print("No winning users found.")
            return

        total_pool = sum(sum(user_bets['amount'] for user_bets in option_bets.values()) for option_bets in self.bets.values())
        total_winning_bets = sum(user_bet_info['amount'] for user_bet_info in self.bets[self.result].values())

        print(f"Total Pool: {total_pool}, Total Winning Bets: {total_winning_bets}")

        if total_winning_bets <= 0:
            print("No valid winning bets to distribute points.")
            return

        for user_id, user_bet_info in winning_users:
            print(f"Calculating payout for User ID: {user_id}, Bet Amount: {user_bet_info['amount']}")
            payout = (user_bet_info['amount'] / total_winning_bets) * total_pool
            payout = int(payout)

            try:
                success = await self.cog.points_manager.add_points(user_id, payout)
                if success:
                    user = await self.cog.bot.fetch_user(user_id)
                    await user.send(
                        f"🎉 You won {payout:,} Points on '{self.question}'!\n"
                        f"Your Bet: {user_bet_info['amount']:,} → Payout: {payout:,}"
                    )
                else:
                    print(f"Failed to add points for User ID: {user_id}.")
            except Exception as e:
                print(f"Error processing payout for User ID: {user_id}: {e}")

        for option in self.options:
            if option != self.result:
                losing_users = self.bets[option].items()
                for user_id, user_bet_info in losing_users:
                    amount = user_bet_info['amount']
                    print(f"Notifying user {user_id} about loss of {amount} points.")
                    try:
                        user = await self.cog.bot.fetch_user(user_id)
                        if user:
                            await user.send(
                                f"💔 You lost your bet of {amount:,} Points on '{self.question}'.\n"
                                f"The winning option was: '{self.result}'."
                            )
                        else:
                            print(f"User ID {user_id} not found.")
                    except Exception as e:
                        print(f"Error sending losing notification to user {user_id}: {e}")

    def get_total_bets(self):
        return self.total_bets

    def get_option_total_bets(self, option):
        return sum(user_bets['amount'] for user_bets in self.bets[option].values()) if option in self.bets else 0

    def get_bet_history(self):
        history = []
        for option, bets in self.bets.items():
            for user_id, user_bet_info in bets.items():
                history.append((user_id, option, user_bet_info['amount']))
        return history

    def mark_as_refunded(self):
        self.refunded = True
        self.resolved = True

    def get_current_prices(self, points_to_spend=100):
        prices = {}
        
        total_bets = sum(sum(user_bets['amount'] for user_bets in self.bets[option].values()) for option in self.options)
        if total_bets == 0:
            base_probability = 100 / len(self.options)
            probabilities = {option: base_probability for option in self.options}
        else:
            probabilities = {
                option: (sum(user_bets['amount'] for user_bets in self.bets[option].values()) / total_bets * 100)
                for option in self.options
            }
        
        for option in self.options:
            shares = self.calculate_shares_for_points(option, points_to_spend)
            price_per_share = points_to_spend / shares if shares > 0 else float('inf')
            
            prices[option] = {
                'price_per_share': price_per_share,
                'potential_shares': shares,
                'potential_payout': points_to_spend if shares > 0 else 0,
                'probability': probabilities[option],
                'total_bets': sum(user_bets['amount'] for user_bets in self.bets[option].values())
            }
        return prices

    def has_voted(self, user_id):
        return user_id in self.user_votes

    def vote(self, user_id, option):
        if option in self.votes:
            self.votes[option].add(user_id)
            self.user_votes.add(user_id)
            asyncio.create_task(self.cog.update_prediction(self))

    def is_resolved(self):
        return self.resolved

class OptionButton(discord.ui.Button):
    def __init__(self, label, prediction, cog, view):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"bet_{label}"
        )
        self.prediction = prediction
        self.cog = cog
        self.option = label
        self.parent_view = view

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_modal(AmountInput(self.prediction, self.option, self.cog))
        except Exception as e:
            print(f"Error in button callback: {e}")
            await interaction.response.send_message("An error occurred while processing your bet.", ephemeral=True)

class AmountInput(discord.ui.Modal, title="Place Your Bet"):
    def __init__(self, prediction, option, cog):
        super().__init__()
        self.prediction = prediction
        self.option = option
        self.cog = cog
        
        self.amount = discord.ui.TextInput(
            label=f"Enter amount to bet on {option}",
            style=discord.TextStyle.short,
            placeholder="Enter bet amount",
            required=True,
            min_length=1,
            max_length=10,
            default="100"
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount.value)
            if amount <= 0:
                await interaction.response.send_message("Amount must be positive!", ephemeral=True)
                return

            if self.prediction.end_time <= datetime.datetime.now(datetime.timezone.utc):
                await interaction.response.send_message("This prediction has already ended!", ephemeral=True)
                return

            balance = await self.cog.points_manager.get_balance(interaction.user.id)
            if balance < amount:
                await interaction.response.send_message(f"You don't have enough Points! Your balance: {balance:,} Points", ephemeral=True)
                return

            shares = self.prediction.calculate_shares_for_points(self.option, amount)
            actual_price_per_share = amount / shares if shares > 0 else float('inf')

            await self.cog.points_manager.transfer_points(interaction.user.id, self.cog.bot.user.id, amount)
            if await self.prediction.place_bet(interaction.user.id, self.option, amount):
                await interaction.response.send_message(
                    f"Bet placed successfully!\n"
                    f"Amount: {amount:,} Points\n"
                    f"Shares received: {shares:.2f}\n"
                    f"Actual price per share: {actual_price_per_share:.2f} Points",
                    ephemeral=True
                )
        except ValueError:
            await interaction.response.send_message("Invalid amount entered!", ephemeral=True)
        except Exception as e:
            print(f"Error in modal submit: {e}")
            await interaction.response.send_message("An error occurred while placing your bet.", ephemeral=True)

class OptionButtonView(discord.ui.View):
    def __init__(self, prediction, cog):
        super().__init__(timeout=None)
        self.prediction = prediction
        self.cog = cog
        self.stored_interaction = None
        self.update_buttons()
        self.update_task = None
        
        self.cog.active_views.add(self)
        self.cog.prediction_to_views.setdefault(self.prediction, []).append(self)
        self.start_auto_update()

    def update_buttons(self):
        self.clear_items()
        for index, option in enumerate(self.prediction.options):
            button = OptionButton(option, self.prediction, self.cog, self)
            button.row = index
            self.add_item(button)

    def start_auto_update(self):
        if not self.update_task:
            self.update_task = asyncio.create_task(self.auto_update_prices())

    def stop_auto_update(self):
        if self.update_task:
            self.update_task.cancel()
            self.update_task = None

    async def auto_update_prices(self):
        try:
            while True:
                await self.refresh_view()
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in auto_update_prices: {e}")

    async def refresh_view(self):
        if self.stored_interaction:
            try:
                if self.prediction.end_time <= datetime.datetime.now(datetime.timezone.utc):
                    self.stop_auto_update()
                    await self.stored_interaction.edit(
                        content="This prediction has ended!",
                        view=None
                    )
                    return

                test_amount = 10
                prices = self.prediction.get_current_prices(test_amount)
                market_info = "**Current Market Status**\n\n"
                
                for option in self.prediction.options:
                    price_info = prices[option]
                    shares = price_info['potential_shares']
                    actual_price = test_amount / shares if shares > 0 else float('inf')
                    
                    market_info += f"**{option}**\n"
                    market_info += f"• Total Bets: {price_info['total_bets']:,} Points\n"
                    market_info += f"• Probability: {price_info['probability']:.1f}%\n"
                    market_info += f"• Current Price: {actual_price:.2f} Points/Share\n\n"
                
                total_volume = self.prediction.get_total_bets()
                market_info += f"\n**Total Volume**: {total_volume:,} Points"
                
                await self.stored_interaction.edit(
                    content=market_info,
                    view=self
                )
            except discord.NotFound:
                self.stop_auto_update()
                self.cog.active_views.discard(self)
            except Exception as e:
                print(f"Error refreshing view: {e}")
                self.stop_auto_update()
                self.cog.active_views.discard(self)

    def __del__(self):
        self.stop_auto_update()
        self.cog.active_views.discard(self)
        if self.prediction in self.cog.prediction_to_views:
            self.cog.prediction_to_views[self.prediction].remove(self)
            if not self.cog.prediction_to_views[self.prediction]:
                del self.cog.prediction_to_views[self.prediction]

class ResolutionButton(discord.ui.Button):
    def __init__(self, option: str, prediction: Prediction, view: 'ResolutionView'):
        super().__init__(
            label=option,
            style=discord.ButtonStyle.primary,
            custom_id=f"resolve_{option}"
        )
        self.option = option
        self.prediction = prediction
        self._view = view

    @property
    def view(self):
        return self._view

    async def callback(self, interaction: discord.Interaction):
        if not self.prediction.has_voted(interaction.user.id):
            self.prediction.vote(interaction.user.id, self.option)

            await interaction.response.send_message(f"You voted for {self.option}", ephemeral=True)

            for child in self.view.children:
                if isinstance(child, ResolutionButton):
                    option_votes = len(self.prediction.votes[child.option])
                    child.label = f"{child.option} ({option_votes})"

            await interaction.message.edit(view=self.view)
            await self.view.cog.update_prediction(self.prediction)

            if len(self.prediction.votes[self.option]) >= 1:
                if not self.prediction.resolved:
                    await self.prediction.async_resolve(self.option)

                    for child in self.view.children:
                        child.disabled = True
                        if child.custom_id == f"resolve_{self.option}":
                            child.style = discord.ButtonStyle.success
                        else:
                            child.style = discord.ButtonStyle.danger

                    await interaction.message.edit(
                        content=f"Market resolved! Winning option: {self.option}",
                        view=self.view
                    )

                    await self.view.cog.update_prediction(self.prediction)
        else:
            await interaction.response.send_message("You have already voted!", ephemeral=True)

class ResolutionView(discord.ui.View):
    def __init__(self, prediction: Prediction, cog: 'Economy'):
        super().__init__(timeout=None)
        self.prediction = prediction
        self.cog = cog
        self.stored_interaction = None
        self.update_task = None

        self.start_auto_update()

        for option in prediction.options:
            self.add_item(ResolutionButton(option, prediction, self))

    def start_auto_update(self):
        if not self.update_task:
            self.update_task = asyncio.create_task(self.auto_update())

    def stop_auto_update(self):
        if self.update_task:
            self.update_task.cancel()
            self.update_task = None

    async def auto_update(self):
        try:
            while True:
                await self.refresh_view()
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in auto-update: {e}")

    async def refresh_view(self):
        if not self.stored_interaction:
            return

        try:
            embed = discord.Embed(
                title=f"Vote to Resolve: {self.prediction.question}",
                description="Please vote for the winning option:",
                color=discord.Color.blue()
            )

            for option in self.prediction.options:
                vote_count = len(self.prediction.votes[option])
                embed.add_field(name=option, value=f"Votes: {vote_count}", inline=False)

            await self.stored_interaction.edit(embed=embed, view=self)

        except discord.NotFound:
            self.stop_auto_update()
            print("Interaction not found, stopping updates.")
        except Exception as e:
            print(f"Error refreshing view: {e}")
            self.stop_auto_update()

class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.points_manager = bot.points_manager
        self.predictions = []
        self.active_views = set()
        self.prediction_to_views = {}

    @app_commands.guild_only()
    @app_commands.command(name="create_prediction", description="Create a new prediction market")
    @app_commands.describe(
        question="The question for the prediction",
        duration="Duration format: days,hours,minutes (e.g., 1,2,30 or ,,30 or 1,,)",
        options="Comma-separated list of prediction options",
        category="Category for the prediction (optional)"
    )
    async def create_prediction(
        self, 
        interaction: discord.Interaction, 
        question: str, 
        options: str, 
        duration: str,
        category: str = None
    ):
        await interaction.response.defer(ephemeral=False)
        
        try:
            options_list = [opt.strip() for opt in options.split(",")]
            if len(options_list) < 2:
                await interaction.followup.send("You need at least two options for a prediction!", ephemeral=True)
                return
            
            duration_parts = duration.split(",")
            if len(duration_parts) != 3:
                await interaction.followup.send("Duration must be in format: days,hours,minutes (e.g., 1,2,30 or ,,30 or 1,,)", ephemeral=True)
                return
            
            days = int(duration_parts[0]) if duration_parts[0].strip() else 0
            hours = int(duration_parts[1]) if duration_parts[1].strip() else 0
            minutes = int(duration_parts[2]) if duration_parts[2].strip() else 0
            
            total_minutes = (days * 24 * 60) + (hours * 60) + minutes
            if total_minutes <= 0:
                await interaction.followup.send("Duration must be greater than 0! Please specify days, hours, or minutes.", ephemeral=True)
                return
            
            # Use UTC-based end_time
            end_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=total_minutes)
            new_prediction = Prediction(question, end_time, options_list, interaction.user.id, self, category)
            self.predictions.append(new_prediction)
            asyncio.create_task(self.schedule_prediction_resolution(new_prediction))

            duration_str = []
            if days > 0:
                duration_str.append(f"{days} day{'s' if days != 1 else ''}")
            if hours > 0:
                duration_str.append(f"{hours} hour{'s' if hours != 1 else ''}")
            if minutes > 0:
                duration_str.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            final_duration = ", ".join(duration_str)

            end_time_unix = int(end_time.timestamp())
            await interaction.followup.send(
                f"Prediction created by {interaction.user.name}:\n"
                f"Question: {question}\n"
                f"Options: {', '.join(options_list)}\n"
                f"Duration: {final_duration}\n"
                f"Ends: <t:{end_time_unix}:f>\n"
                f"Category: {category if category else 'None'}",
                ephemeral=True
            )
            
        except ValueError:
            await interaction.followup.send("Invalid duration format! Please use numbers in format: days,hours,minutes (e.g., 1,2,30 or ,,30 or 1,,)", ephemeral=True)
        except Exception as e:
            try:
                await interaction.followup.send(f"Error creating prediction: {str(e)}", ephemeral=True)
            except:
                print(f"Failed to send error message: {str(e)}")

    async def schedule_prediction_resolution(self, prediction: Prediction):
        try:
            time_until_betting_ends = (prediction.end_time - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            if time_until_betting_ends > 0:
                print(f"DEBUG: Waiting {time_until_betting_ends} seconds for betting to end")
                await asyncio.sleep(time_until_betting_ends)
            
            if prediction.resolved:
                print("DEBUG: Prediction already resolved before betting end")
                return
                
            print(f"DEBUG: Betting period ended for {prediction.question}")
            
            try:
                creator = await self.bot.fetch_user(prediction.creator_id)
                await creator.send(
                    f"🎲 Betting has ended for your prediction: '{prediction.question}'\n"
                    f"Please use `/resolve_prediction` to resolve the market.\n"
                    f"If not resolved within 5 days, all bets will be automatically refunded."
                )
                print(f"DEBUG: Sent notification to creator {prediction.creator_id}")
            except Exception as e:
                print(f"DEBUG: Error notifying creator: {e}")

            print("DEBUG: Starting 120-hour wait")
            await asyncio.sleep(120 * 3600)
            
            if prediction.resolved:
                print("DEBUG: Prediction resolved during 5-day wait")
                return
                
            print("DEBUG: Starting auto-refund process")
            
            prediction.mark_as_refunded()
            
            for option in prediction.bets:
                for user_id, user_bet_info in prediction.bets[option].items():
                    await self.points_manager.add_points(user_id, user_bet_info['amount'])
                    try:
                        user = await self.bot.fetch_user(user_id)
                        await user.send(
                            f"💰 Your bet of {user_bet_info['amount']:,} Points has been refunded for the expired market:\n"
                            f"'{prediction.question}'"
                        )
                    except Exception as e:
                        print(f"DEBUG: Error sending refund notification: {e}")
                    
        except Exception as e:
            print(f"DEBUG: Error in schedule_prediction_resolution: {e}")

    @app_commands.guild_only()
    @app_commands.command(name="bet", description="Place a bet on a prediction")
    async def bet(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        now = datetime.datetime.now(datetime.timezone.utc)
        active_predictions = [prediction for prediction in self.predictions if not prediction.resolved and prediction.end_time > now]
        if not active_predictions:
            await interaction.followup.send("No active predictions at the moment.", ephemeral=True)
            return

        categories = list(set(prediction.category for prediction in active_predictions if prediction.category))
        categories.append("All")

        class CategoryButton(discord.ui.Button):
            def __init__(self, label, cog):
                super().__init__(label=label, style=discord.ButtonStyle.primary)
                self.cog = cog
                self.category = label

            async def callback(self, button_interaction: discord.Interaction):
                await button_interaction.response.defer(ephemeral=True)

                if self.category == "All":
                    filtered_predictions = active_predictions
                else:
                    filtered_predictions = [p for p in active_predictions if p.category == self.category]

                if not filtered_predictions:
                    await button_interaction.followup.send("No predictions available for this category.", ephemeral=True)
                    return

                class PredictionSelect(discord.ui.Select):
                    def __init__(self, predictions, cog):
                        self.cog = cog
                        options = [
                            discord.SelectOption(
                                label=prediction.question, 
                                description=f"Ends at {prediction.end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}", 
                                value=str(index)
                            )
                            for index, prediction in enumerate(predictions)
                        ]
                        super().__init__(placeholder="Select a prediction to bet on...", min_values=1, max_values=1, options=options)

                    async def callback(self, interaction: discord.Interaction):
                        await interaction.response.defer(ephemeral=True)
                        selected_index = int(self.values[0])
                        selected_prediction = filtered_predictions[selected_index]

                        if selected_prediction.end_time <= datetime.datetime.now(datetime.timezone.utc):
                            await interaction.followup.send("This prediction has already ended!", ephemeral=True)
                            return

                        view = OptionButtonView(selected_prediction, self.cog)
                        prices = selected_prediction.get_current_prices(100)
                        
                        market_info = "**Current Market Prices**\n\n"
                        for option in selected_prediction.options:
                            price = prices[option]['price_per_share']
                            market_info += f"{option}: {price:.2f} Points/Share\n"
                        
                        message = await interaction.followup.send(
                            content=market_info,
                            view=view, 
                            ephemeral=True,
                            wait=True
                        )
                        view.stored_interaction = message

                class PredictionSelectView(discord.ui.View):
                    def __init__(self, predictions, cog):
                        super().__init__()
                        select = PredictionSelect(predictions, cog)
                        self.add_item(select)

                await button_interaction.followup.send(
                    content="Please select a prediction to bet on:", 
                    view=PredictionSelectView(filtered_predictions, self.cog),
                    ephemeral=True
                )

        class CategoryButtonView(discord.ui.View):
            def __init__(self, categories, cog):
                super().__init__()
                for category in categories:
                    button = CategoryButton(label=category, cog=cog)
                    self.add_item(button)

        await interaction.followup.send("Please select a category:", view=CategoryButtonView(categories, self))

    @app_commands.guild_only()
    @app_commands.command(name="list_predictions", description="List all active predictions")
    async def list_predictions(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            
            for view in list(self.active_views):
                if isinstance(view, ListPredictionsView):
                    view.stop_auto_update()
                    self.active_views.discard(view)

            view = ListPredictionsView(self)
            self.active_views.add(view)

            current_embed = discord.Embed(
                title="🎲 Prediction Markets",
                description="Current prediction markets available for betting.",
                color=discord.Color.blue()
            )

            now = datetime.datetime.now(datetime.timezone.utc)
            active_markets = []
            inactive_markets = []
            resolved_markets = []
            refunded_markets = []
            pending_resolution_markets = []

            for prediction in self.predictions:
                prices = prediction.get_current_prices(100)
                combined_data = (prediction.question, prediction, prices, prediction.creator_id)

                if prediction.resolved:
                    if prediction.refunded:
                        refunded_markets.append(combined_data)
                    else:
                        resolved_markets.append(combined_data)
                elif prediction.end_time <= now:
                    inactive_markets.append(combined_data)
                else:
                    active_markets.append(combined_data)

                if prediction.end_time <= now and not prediction.resolved:
                    pending_resolution_markets.append(combined_data)

            if active_markets:
                current_embed.add_field(name=" Active Markets", value="\u200b", inline=False)
                for question, prediction, prices, creator_id in active_markets:
                    creator_name = (await self.bot.fetch_user(creator_id)).name
                    end_time_unix = int(prediction.end_time.timestamp())
                    current_embed.add_field(
                        name=f"📊 {question} (Created by: {creator_name})",
                        value=view.create_market_display(prediction, prices, end_time_unix),
                        inline=False
                    )

            if pending_resolution_markets:
                current_embed.add_field(name="🟡 Pending Resolution", value="\u200b", inline=False)
                for question, prediction, prices, creator_id in pending_resolution_markets:
                    creator_name = (await self.bot.fetch_user(creator_id)).name
                    end_time_unix = int(prediction.end_time.timestamp())
                    current_embed.add_field(
                        name=f"📊 {question} (Created by: {creator_name})",
                        value=view.create_market_display(prediction, prices, end_time_unix),
                        inline=False
                    )

            current_embed.set_footer(text="Use /bet to place bets on active markets")
            message = await interaction.followup.send(embed=current_embed, ephemeral=True)
            view.stored_interaction = message
            
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)

        await asyncio.sleep(300)
        if view in self.active_views:
            view.stop_auto_update()
            self.active_views.discard(view)

    @app_commands.guild_only()
    @app_commands.command(name="resolve_prediction", description="Vote to resolve a prediction")
    async def resolve_prediction_command(self, interaction: discord.Interaction):
        allowed_role_ids = {1301959367536672838, 1301958607046443018, 1301958999092236389}
        user_roles = {role.id for role in interaction.user.roles}

        if not user_roles.intersection(allowed_role_ids):
            await interaction.response.send_message("You do not have permission to resolve predictions.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.datetime.now(datetime.timezone.utc)
        unresolved_predictions = [
            pred for pred in self.predictions
            if not pred.resolved and pred.end_time <= now
        ]

        if not unresolved_predictions:
            await interaction.followup.send(
                "There are no predictions ready to be resolved.",
                ephemeral=True
            )
            return

        class PredictionSelect(discord.ui.Select):
            def __init__(self, predictions, cog):
                self.cog = cog
                options = [
                    discord.SelectOption(
                        label=prediction.question[:100],
                        description=f"Ended {prediction.end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                        value=str(index)
                    )
                    for index, prediction in enumerate(predictions)
                ]
                super().__init__(
                    placeholder="Select a prediction to resolve...",
                    min_values=1,
                    max_values=1,
                    options=options
                )

            async def callback(self, interaction: discord.Interaction):
                selected_index = int(self.values[0])
                selected_prediction = unresolved_predictions[selected_index]

                view = ResolutionView(selected_prediction, self.cog)
                embed = discord.Embed(
                    title=f"Vote to Resolve: {selected_prediction.question}",
                    description="Please vote for the winning option:",
                    color=discord.Color.blue()
                )

                await interaction.response.send_message(embed=embed, view=view)
                view.stored_interaction = await interaction.original_response()

        view = discord.ui.View()
        view.add_item(PredictionSelect(unresolved_predictions, self))
        await interaction.followup.send("Select a prediction to resolve:", view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_prediction_update(self, prediction: Prediction):
        if prediction in self.prediction_to_views:
            views = self.prediction_to_views[prediction]
            for v in views:
                if v.stored_interaction:
                    try:
                        await v.refresh_view()
                    except Exception as e:
                        print(f"Error updating view: {e}")
                        self.active_views.discard(v)

    async def update_prediction(self, prediction: Prediction):
        await self.on_prediction_update(prediction)

    async def place_bet(self, user_id, prediction, option, amount):
        try:
            success = prediction.place_bet(user_id, option, amount)
            if success:
                await self.update_prediction(prediction)
            return success
        except Exception as e:
            print(f"Error placing bet: {e}")
            return False

    async def cleanup_old_views(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for view in list(self.active_views):
            if hasattr(view, 'prediction'):
                if view.prediction.resolved or view.prediction.end_time <= now:
                    view.stop_auto_update()
                    self.active_views.discard(view)

class ListPredictionsView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.stored_interaction = None
        self.update_task = None
        self.start_auto_update()

        for prediction in self.cog.predictions:
            self.cog.prediction_to_views.setdefault(prediction, []).append(self)

    def start_auto_update(self):
        if not self.update_task:
            self.update_task = asyncio.create_task(self.auto_update())

    def stop_auto_update(self):
        if self.update_task:
            self.update_task.cancel()
            self.update_task = None

    async def auto_update(self):
        try:
            while True:
                await self.refresh_view()
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in auto-update: {e}")

    async def refresh_view(self):
        if not self.stored_interaction:
            return

        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            current_embed = discord.Embed(
                title="🎲 Prediction Markets",
                description="Current prediction markets available for betting.",
                color=discord.Color.blue()
            )

            active_markets = []
            pending_resolution_markets = []

            for prediction in self.cog.predictions:
                prices = prediction.get_current_prices(100)
                combined_data = (prediction.question, prediction, prices, prediction.creator_id)

                if prediction.resolved:
                    continue
                elif now >= prediction.end_time and not prediction.resolved:
                    pending_resolution_markets.append(combined_data)
                elif now < prediction.end_time and not prediction.resolved:
                    active_markets.append(combined_data)

            if active_markets:
                current_embed.add_field(name="🟢 Active Markets", value="\u200b", inline=False)
                for question, prediction, prices, creator_id in active_markets:
                    creator_name = (await self.cog.bot.fetch_user(creator_id)).name
                    end_time_unix = int(prediction.end_time.timestamp())
                    current_embed.add_field(
                        name=f"📊 {question} (Created by: {creator_name})",
                        value=self.create_market_display(prediction, prices, end_time_unix),
                        inline=False
                    )

            if pending_resolution_markets:
                current_embed.add_field(name="🟡 Pending Resolution", value="\u200b", inline=False)
                for question, prediction, prices, creator_id in pending_resolution_markets:
                    creator_name = (await self.cog.bot.fetch_user(creator_id)).name
                    end_time_unix = int(prediction.end_time.timestamp())
                    current_embed.add_field(
                        name=f"📊 {question} (Created by: {creator_name})",
                        value=self.create_market_display(prediction, prices, end_time_unix),
                        inline=False
                    )

            await self.stored_interaction.edit(embed=current_embed)

        except discord.NotFound:
            self.stop_auto_update()
            if self in self.cog.active_views:
                self.cog.active_views.discard(self)
        except Exception as e:
            print(f"Error refreshing list view: {e}")
            self.stop_auto_update()

    def create_market_display(self, prediction, prices, end_time_unix):
        market_text = (
            f"**Category:** {prediction.category or 'None'}\n"
            f"**Total Volume:** {prediction.get_total_bets():,} Points\n"
            f"**Ends:** <t:{end_time_unix}:f>\n"  # Full time format
            f"\n**Current Market Status:**\n"
        )

        for option in prediction.options:
            price_info = prices[option]
            vote_count = len(prediction.votes[option])
            market_text += (
                f"```\n"
                f"{option}\n"
                f"Price: {price_info['price_per_share']:.2f} Points/Share\n"
                f"Prob:  {price_info['probability']:.1f}%\n"
                f"Volume: {price_info['total_bets']:,} Points\n"
                f"Votes: {vote_count}\n"
                f"```\n"
            )

        return market_text

class PointsManagerSingleton:
    def __init__(self, session, base_url, realm_id):
        self.session = session
        self.base_url = base_url
        self.realm_id = realm_id

    async def add_points(self, user_id: int, amount: int) -> bool:
        if not self.session:
            await self.initialize()
        
        headers = await self._get_headers()
        
        try:
            async with self.session.patch(
                f"{self.base_url}/api/v4/realms/{self.realm_id}/members/{user_id}/tokenBalance",
                headers=headers,
                json={"tokens": amount}
            ) as response:
                if response.status == 200:
                    return True
                else:
                    response_text = await response.text()
                    print(f"Failed to add points: {response.status} - {response_text}")
                    return False
        except Exception as e:
            print(f"Error adding points to user {user_id}: {e}")
            return False

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
