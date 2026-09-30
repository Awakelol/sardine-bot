import json

import discord

import json_store
import log_utils

CONFIG_PATH = "roles_config.json"


def load_config():
    with open(json_store.config_path(CONFIG_PATH), "r", encoding="utf-8") as f:
        return json.load(f)


class CategorySelect(discord.ui.Select):
    """Dropdown for a single role category, e.g. 'Genre Interests'."""

    def __init__(self, category_name: str, category_data: dict):
        self.category_name = category_name
        self.role_names = [r["role_name"] for r in category_data["roles"]]
        self.divider_role = category_data.get("divider_role")

        options = [
            discord.SelectOption(label=r["label"], emoji=r.get("emoji"), value=r["role_name"])
            for r in category_data["roles"]
        ]

        super().__init__(
            placeholder=f"Select your {category_name} roles...",
            min_values=0,
            max_values=min(category_data.get("max_values", len(options)), len(options)),
            options=options,
            custom_id=f"role_select:{category_name}",
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        selected = set(self.values)

        category_roles = {name: discord.utils.get(guild.roles, name=name) for name in self.role_names}
        missing = [name for name, role in category_roles.items() if role is None]
        if missing:
            await interaction.response.send_message(
                f"⚠️ These roles don't exist in the server yet, ask an admin to create them: {', '.join(missing)}",
                ephemeral=True,
            )
            return

        to_add = [role for name, role in category_roles.items() if name in selected and role not in member.roles]
        to_remove = [role for name, role in category_roles.items() if name not in selected and role in member.roles]

        # member.roles isn't updated by add_roles/remove_roles, so work out the new set ourselves
        new_role_names = {r.name for r in member.roles if r not in to_remove} | {r.name for r in to_add}

        try:
            if to_add:
                await member.add_roles(*to_add, reason="Role menu selection")
            if to_remove:
                await member.remove_roles(*to_remove, reason="Role menu deselection")
            await self._sync_divider(guild, member, new_role_names)
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to change those roles, let an admin know.", ephemeral=True
            )
            return

        added = ", ".join(r.name for r in to_add)
        removed = ", ".join(r.name for r in to_remove)

        summary = []
        if added:
            summary.append(f"Added: {added}")
        if removed:
            summary.append(f"Removed: {removed}")
        await interaction.response.send_message(
            f"✅ **{self.category_name}** updated.\n" + ("\n".join(summary) or "No changes made."),
            ephemeral=True,
        )

        if added or removed:
            changes = []
            if added:
                changes.append(f"picked up **{added}**")
            if removed:
                changes.append(f"removed **{removed}**")
            await log_utils.send_log(
                interaction.client,
                guild,
                f"🎛️ {member.mention} {' and '.join(changes)} ({self.category_name})",
            )

    async def _sync_divider(self, guild: discord.Guild, member: discord.Member, role_names: set[str]):
        # The divider stays as long as the member has a role from any category sharing it
        if not self.divider_role:
            return

        divider = discord.utils.get(guild.roles, name=self.divider_role)
        if divider is None:
            return

        sibling_role_names = {
            r["role_name"]
            for cat_data in load_config().values()
            if cat_data.get("divider_role") == self.divider_role
            for r in cat_data["roles"]
        }
        has_any = not role_names.isdisjoint(sibling_role_names)

        if has_any and divider not in member.roles:
            await member.add_roles(divider, reason="Category divider sync")
        elif not has_any and divider in member.roles:
            await member.remove_roles(divider, reason="Category divider sync")


class RolePickerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for category_name, category_data in load_config().items():
            self.add_item(CategorySelect(category_name, category_data))


class RoleButtonView(discord.ui.View):
    """The persistent button that opens the picker."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Manage your roles", style=discord.ButtonStyle.blurple, emoji="🎛️", custom_id="open_role_picker"
    )
    async def open_picker(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Pick your roles below. Changes save automatically per category.",
            view=RolePickerView(),
            ephemeral=True,
        )
