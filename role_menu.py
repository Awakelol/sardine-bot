import json
import discord

import log_utils

CONFIG_PATH = "roles_config.json"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class CategorySelect(discord.ui.Select):
    """A single dropdown for one role category (e.g. 'Interests')."""

    def __init__(self, category_name: str, category_data: dict):
        self.category_name = category_name
        self.role_names = [r["role_name"] for r in category_data["roles"]]
        self.divider_role = category_data.get("divider_role")

        options = [
            discord.SelectOption(
                label=r["label"],
                emoji=r.get("emoji"),
                value=r["role_name"]
            )
            for r in category_data["roles"]
        ]

        super().__init__(
            placeholder=f"Select your {category_name} roles...",
            min_values=0,
            max_values=min(category_data.get("max_values", len(options)), len(options)),
            options=options,
            custom_id=f"role_select:{category_name}"
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        selected = set(self.values)

        # Find the actual Role objects in the server for this category
        category_roles = {
            name: discord.utils.get(guild.roles, name=name)
            for name in self.role_names
        }

        missing = [name for name, role in category_roles.items() if role is None]
        if missing:
            await interaction.response.send_message(
                f"⚠️ These roles don't exist in the server yet, ask an admin to create them: {', '.join(missing)}",
                ephemeral=True
            )
            return

        to_add = [role for name, role in category_roles.items() if name in selected and role not in member.roles]
        to_remove = [role for name, role in category_roles.items() if name not in selected and role in member.roles]

        if to_add:
            await member.add_roles(*to_add, reason="Role menu selection")
        if to_remove:
            await member.remove_roles(*to_remove, reason="Role menu deselection")

        await self._sync_divider(guild, member)

        if to_add or to_remove:
            log_parts = []
            if to_add:
                log_parts.append("picked up **" + ", ".join(r.name for r in to_add) + "**")
            if to_remove:
                log_parts.append("removed **" + ", ".join(r.name for r in to_remove) + "**")
            await log_utils.send_log(
                interaction.client, guild,
                f"🎛️ {member.mention} {' and '.join(log_parts)} ({self.category_name})"
            )

        summary = []
        if to_add:
            summary.append("Added: " + ", ".join(r.name for r in to_add))
        if to_remove:
            summary.append("Removed: " + ", ".join(r.name for r in to_remove))
        if not summary:
            summary.append("No changes made.")

        await interaction.response.send_message(
            f"✅ **{self.category_name}** updated.\n" + "\n".join(summary),
            ephemeral=True
        )

    async def _sync_divider(self, guild: discord.Guild, member: discord.Member):
        """Add/remove this category's divider role based on whether the member still
        holds any role from any category that shares that same divider."""
        if not self.divider_role:
            return

        config = load_config()
        sibling_role_names = {
            r["role_name"]
            for cat_data in config.values()
            if cat_data.get("divider_role") == self.divider_role
            for r in cat_data["roles"]
        }

        divider = discord.utils.get(guild.roles, name=self.divider_role)
        if divider is None:
            return  # admin hasn't created/renamed the divider role yet

        has_any = any(name in sibling_role_names for name in (r.name for r in member.roles))

        if has_any and divider not in member.roles:
            await member.add_roles(divider, reason="Category divider sync")
        elif not has_any and divider in member.roles:
            await member.remove_roles(divider, reason="Category divider sync")


class RolePickerView(discord.ui.View):
    """The full set of dropdowns shown when a user opens the role picker."""

    def __init__(self):
        super().__init__(timeout=None)
        config = load_config()
        for category_name, category_data in config.items():
            self.add_item(CategorySelect(category_name, category_data))


class RoleButtonView(discord.ui.View):
    """The persistent button posted in a channel that opens the picker."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Manage your roles",
        style=discord.ButtonStyle.blurple,
        emoji="🎛️",
        custom_id="open_role_picker"
    )
    async def open_picker(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Pick your roles below. Changes save automatically per category.",
            view=RolePickerView(),
            ephemeral=True
        )
