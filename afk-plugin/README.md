# AFKVerify — Spigot/Paper Plugin

Replicates the **JartexNetwork "Afk Grinding" popup** so you can test `mc_farm.sh` on your own server.

## What it does

- Monitors each player's position every second
- If a player hasn't moved **≥ afk-distance blocks** in **afk-seconds seconds**, it opens a 3×9 inventory GUI titled `§8Afk Grinding`
- One random slot contains a **§a§l`Click to Confirm`** item (LIME_DYE = green)
- The other 26 slots contain **§c§l`Do not click`** items (RED_DYE = red)
- Correct click → popup closes, timer resets
- Wrong click → kick (configurable)
- No response within `kick-timeout` seconds → kick

## Install

1. Drop `AFKVerify.jar` into your server's `plugins/` folder
2. Start/restart the server
3. Edit `plugins/AFKVerify/config.yml` to adjust thresholds
4. Use `/afkverify reload` to apply without restarting

## Works on

| Platform | Supported |
|---|---|
| **Aternos** (free hosting) | ✅ Upload via Plugins → Custom |
| **Local Spigot server** | ✅ |
| **Local Paper server** | ✅ (recommended) |
| **Fabric / Forge** | ❌ (Spigot/Paper only) |

## Config (`plugins/AFKVerify/config.yml`)

```yaml
afk-distance: 0.5    # blocks player must move to reset timer
                     # 0.5 = looking around triggers it; 2.0 = must actually walk

afk-seconds: 30      # idle time before popup appears (seconds)
                     # 30 = good for testing; 120 = matches JartexNetwork

kick-timeout: 15     # seconds to click the correct item before kick
                     # set >= 10 so mc_farm.sh has time to detect + click

kick-on-fail: true   # kick on wrong click? false = just warn + reset timer

deny-slots: 26       # how many red "Do not click" items to show (0–26)
                     # 26 = full grid (matches JartexNetwork)
```

## Commands

| Command | Permission | Description |
|---|---|---|
| `/afkverify test` | `afkverify.admin` | Trigger popup on yourself immediately |
| `/afkverify status` | `afkverify.admin` | Show current config values |
| `/afkverify reload` | `afkverify.admin` | Reload config without restart |

## How to run a local test server (Linux)

```bash
# 1. Download Paper (latest 1.20.x)
mkdir mc-test && cd mc-test
wget -O paper.jar https://api.papermc.io/v2/projects/paper/versions/1.20.4/builds/496/downloads/paper-1.20.4-496.jar

# 2. Accept EULA
echo "eula=true" > eula.txt

# 3. Start server (first time to generate files)
java -Xmx1G -jar paper.jar nogui

# 4. Drop AFKVerify.jar into plugins/
cp ../afk-plugin/AFKVerify.jar plugins/

# 5. Restart server
java -Xmx1G -jar paper.jar nogui
```

Then connect with Minecraft Java Edition (offline mode: `java -jar paper.jar --nogui` sets up a local server on `localhost:25565`).

## Aternos setup

1. Log in to [aternos.org](https://aternos.org)
2. Create a server → choose **Paper** (any 1.20.x version)
3. Go to **Plugins** → **Custom** → upload `AFKVerify.jar`
4. Start the server, join, and run `/afkverify test`

## Testing with mc_farm.sh

Once the popup appears in-game:

```bash
# On your Linux machine with the game open:
bash mc_farm.sh start

# Watch the log:
tail -f /tmp/mc_afk_log.txt
```

The script will detect the gray inventory, prescan slots, hover each item to read the tooltip color, and click the green one.

## Build from source

```bash
cd afk-plugin
bash build.sh     # downloads Paper API, compiles, outputs AFKVerify.jar
```

Requires Java 8+.
