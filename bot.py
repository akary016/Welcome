import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

try:
    import libsql
except ImportError:
    libsql = None
import sqlite3

TOKEN = os.getenv("DISCORD_TOKEN")


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # silencia os logs de HTTP no console


def start_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Servidor de health check ouvindo na porta {port}")


intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="*", intents=intents)

# ---------- Tema visual ----------
COLOR_PRIMARY = discord.Color.from_rgb(155, 89, 217)   # roxo
COLOR_SUCCESS = discord.Color.from_rgb(87, 242, 135)   # verde
COLOR_DANGER = discord.Color.from_rgb(237, 66, 69)     # vermelho
BOT_FOOTER = "⭐ Servidor Bot"

# ---------- Banco de dados ----------
DB_PATH = os.getenv("DB_PATH", "bot.db")
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

if TURSO_DATABASE_URL and libsql is not None:
    conn = libsql.connect(DB_PATH, sync_url=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
    conn.sync()
    print("Conectado ao Turso (banco remoto persistente).")
else:
    conn = sqlite3.connect(DB_PATH)
    print("TURSO_DATABASE_URL não definida — usando SQLite local (não persiste em deploys no Render).")

cur = conn.cursor()
cur.execute(
    """
    CREATE TABLE IF NOT EXISTS config (
        guild_id INTEGER PRIMARY KEY,
        welcome_channel_id INTEGER,
        staff_message TEXT
    )
    """
)
conn.commit()

DEFAULT_STAFF_MESSAGE = "📋 Quer fazer parte da equipe? Entre na staff e ajude a construir a comunidade!"


def get_welcome_channel(guild_id: int):
    cur.execute("SELECT welcome_channel_id FROM config WHERE guild_id=?", (guild_id,))
    row = cur.fetchone()
    return row[0] if row else None


def set_welcome_channel(guild_id: int, channel_id: int):
    cur.execute(
        "INSERT INTO config (guild_id, welcome_channel_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET welcome_channel_id=?",
        (guild_id, channel_id, channel_id),
    )
    conn.commit()


def get_staff_message(guild_id: int):
    cur.execute("SELECT staff_message FROM config WHERE guild_id=?", (guild_id,))
    row = cur.fetchone()
    if row and row[0]:
        return row[0]
    return DEFAULT_STAFF_MESSAGE


def set_staff_message(guild_id: int, message: str):
    cur.execute(
        "INSERT INTO config (guild_id, staff_message) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET staff_message=?",
        (guild_id, message, message),
    )
    conn.commit()


# ---------- Eventos ----------

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot conectado como {bot.user}")


@bot.event
async def on_member_join(member: discord.Member):
    channel_id = get_welcome_channel(member.guild.id)
    if channel_id:
        channel = member.guild.get_channel(channel_id)
        if channel:
            staff_msg = get_staff_message(member.guild.id)
            embed = discord.Embed(
                title="🎉 Novo membro!",
                description=f"Bem-vindo(a) ao servidor, {member.mention}!\n\n{staff_msg}",
                color=COLOR_PRIMARY,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=BOT_FOOTER)
            await channel.send(embed=embed, delete_after=15)


# ---------- Configuração ----------

@bot.tree.command(name="setwelcome", description="Define o canal de boas-vindas (admin)")
@app_commands.describe(canal="Canal para as mensagens de boas-vindas")
@app_commands.checks.has_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction, canal: discord.TextChannel):
    set_welcome_channel(interaction.guild.id, canal.id)
    embed = discord.Embed(description=f"✅ Canal de boas-vindas definido para {canal.mention}", color=COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="setstaffmsg", description="Define a mensagem extra de recrutamento na boas-vindas (admin)")
@app_commands.describe(mensagem="Texto que aparece embaixo da boas-vindas, ex: convite pra staff")
@app_commands.checks.has_permissions(administrator=True)
async def setstaffmsg(interaction: discord.Interaction, mensagem: str):
    set_staff_message(interaction.guild.id, mensagem)
    embed = discord.Embed(
        description=f"✅ Mensagem de recrutamento atualizada:\n\n{mensagem}",
        color=COLOR_SUCCESS,
    )
    await interaction.response.send_message(embed=embed)


# ---------- Moderação ----------

@bot.tree.command(name="kick", description="Expulsa um membro (mod)")
@app_commands.describe(membro="Membro a expulsar", motivo="Motivo")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não especificado"):
    await membro.kick(reason=motivo)
    embed = discord.Embed(description=f"👋 {membro.mention} foi expulso. Motivo: {motivo}", color=COLOR_DANGER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ban", description="Bane um membro (mod)")
@app_commands.describe(membro="Membro a banir", motivo="Motivo")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não especificado"):
    await membro.ban(reason=motivo)
    embed = discord.Embed(description=f"🔨 {membro.mention} foi banido. Motivo: {motivo}", color=COLOR_DANGER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="mute", description="Silencia um membro por X minutos (mod)")
@app_commands.describe(membro="Membro a silenciar", minutos="Duração em minutos")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, membro: discord.Member, minutos: int):
    duracao = timedelta(minutes=minutos)
    await membro.timeout(duracao)
    embed = discord.Embed(description=f"🔇 {membro.mention} foi silenciado por {minutos} minutos.", color=COLOR_DANGER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unmute", description="Remove o silenciamento de um membro (mod)")
@app_commands.describe(membro="Membro")
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, membro: discord.Member):
    await membro.timeout(None)
    embed = discord.Embed(description=f"🔊 {membro.mention} não está mais silenciado.", color=COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="clear", description="Limpa mensagens no canal (mod)")
@app_commands.describe(quantidade="Quantidade de mensagens a apagar (máx 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=quantidade)
    await interaction.followup.send(f"🧹 {len(deleted)} mensagens apagadas.", ephemeral=True)


@bot.tree.command(name="warn", description="Adverte um membro (mod)")
@app_commands.describe(membro="Membro", motivo="Motivo")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, membro: discord.Member, motivo: str):
    try:
        await membro.send(f"⚠️ Você recebeu uma advertência em **{interaction.guild.name}**. Motivo: {motivo}")
    except discord.Forbidden:
        pass
    embed = discord.Embed(description=f"⚠️ {membro.mention} foi advertido. Motivo: {motivo}", color=COLOR_DANGER)
    await interaction.response.send_message(embed=embed)


# ---------- Tratamento de erros de permissão ----------

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("Você não tem permissão para usar esse comando.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Ocorreu um erro: {error}", ephemeral=True)
        raise error


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Defina a variável de ambiente DISCORD_TOKEN antes de rodar o bot.")
    start_health_server()
    bot.run(TOKEN)
