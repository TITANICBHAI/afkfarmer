package com.afkverify;

import org.bukkit.*;
import org.bukkit.command.*;
import org.bukkit.configuration.file.FileConfiguration;
import org.bukkit.entity.Player;
import org.bukkit.event.*;
import org.bukkit.event.inventory.*;
import org.bukkit.event.player.*;
import org.bukkit.inventory.*;
import org.bukkit.inventory.meta.ItemMeta;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitRunnable;

import java.io.FileWriter;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;

/**
 * AFKVerify — Spigot/Paper plugin that replicates the JartexNetwork "Afk Grinding" popup.
 *
 * How it works:
 *   1. Tracks each online player's position every second.
 *   2. If a player hasn't moved more than [afk-distance] blocks in [afk-seconds] seconds,
 *      it opens a 3-row × 9-col inventory GUI titled "§8Afk Grinding".
 *   3. One random slot gets a LIME_DYE with §a§lClick to Confirm lore.
 *      The other 26 slots get RED_DYE with §c§lDo not click lore.
 *   4. Correct click → GUI closes, timer resets.
 *      Wrong click   → kick (if kick-on-fail: true).
 *      No response within [kick-timeout] seconds → kick.
 *
 * All thresholds are in config.yml and reloadable via /afkverify reload.
 *
 * Every popup also writes an afkverify_events.jsonl record (same schema as
 * the Fabric/Forge mods: popup_id join key, confirm_row/col, clicked_row/col)
 * so it can be joined against mc_farm.sh's attempts.jsonl by join_training_data.py.
 *
 * Tested against: Spigot/Paper 1.20.x (API level 1.13+)
 */
public class AFKVerifyPlugin extends JavaPlugin implements Listener {

    // ── state maps ────────────────────────────────────────────────────────
    private final Map<UUID, Location>  lastLoc        = new HashMap<>();
    private final Map<UUID, Long>      idleSince      = new HashMap<>();  // System.currentTimeMillis
    private final Map<UUID, Inventory> openGuis       = new HashMap<>();  // player → open GUI
    private final Map<UUID, BukkitRunnable> kickTasks = new HashMap<>();  // pending kick timers
    private final Map<UUID, Integer>   confirmSlotMap = new HashMap<>();  // player → correct slot this popup
    private final Map<UUID, Long>      guiOpenAt      = new HashMap<>();  // player → popup open time (ms)
    private final Map<UUID, String>    popupIdMap     = new HashMap<>();  // player → popup_id (join key)

    private static final DateTimeFormatter ISO = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss");
    private static final String EVENT_LOG = "afkverify_events.jsonl";
    private static final String PLATFORM  = "paper";

    // ── JSON event writer — mirrors afk-fabric/afk-forge AFKPlayerTracker ──
    private static synchronized void writeEvent(String json) {
        try (FileWriter fw = new FileWriter(EVENT_LOG, true)) {
            fw.write(json + "\n");
        } catch (Exception ignored) {
            // Non-fatal: training-data logging must never break AFK verification itself.
        }
    }

    private static String nowIso() {
        return LocalDateTime.now().format(ISO);
    }

    private static double nowEpoch() {
        return System.currentTimeMillis() / 1000.0;
    }

    private static String esc(String s) {
        return s == null ? "null" : "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    // ── config cache ──────────────────────────────────────────────────────
    private double afkDistance;   // blocks (3D distance)
    private long   afkSeconds;    // seconds before popup triggers
    private int    kickTimeout;   // seconds to answer before kick
    private boolean kickOnFail;   // kick on wrong click
    private int    denySlots;     // number of "Do not click" items (rest of 27)

    @Override
    public void onEnable() {
        saveDefaultConfig();
        loadConfig();

        getServer().getPluginManager().registerEvents(this, this);
        startMovementTracker();

        // /afkverify command
        PluginCommand cmd = getCommand("afkverify");
        if (cmd != null) cmd.setExecutor(new CommandHandler());

        getLogger().info("AFKVerify enabled. afk-distance=" + afkDistance
                + "b  afk-seconds=" + afkSeconds + "s  kick-timeout=" + kickTimeout + "s");
    }

    @Override
    public void onDisable() {
        kickTasks.forEach((id, task) -> task.cancel());
    }

    // ══════════════════════════════════════════════════════════════════════
    //  Config helpers
    // ══════════════════════════════════════════════════════════════════════
    private void loadConfig() {
        FileConfiguration c = getConfig();
        afkDistance = c.getDouble("afk-distance", 0.5);
        afkSeconds  = c.getLong("afk-seconds",    30);
        kickTimeout = c.getInt("kick-timeout",     15);
        kickOnFail  = c.getBoolean("kick-on-fail", true);
        denySlots   = Math.max(0, Math.min(26, c.getInt("deny-slots", 26)));
    }

    // ══════════════════════════════════════════════════════════════════════
    //  Movement tracker — fires every second
    // ══════════════════════════════════════════════════════════════════════
    private void startMovementTracker() {
        new BukkitRunnable() {
            @Override public void run() {
                long now = System.currentTimeMillis();
                for (Player p : Bukkit.getOnlinePlayers()) {
                    UUID id = p.getUniqueId();
                    if (openGuis.containsKey(id)) continue;  // already in GUI

                    Location cur = p.getLocation();
                    Location prev = lastLoc.get(id);

                    boolean moved = false;
                    if (prev != null && prev.getWorld() != null
                            && prev.getWorld().equals(cur.getWorld())) {
                        moved = prev.distance(cur) >= afkDistance;
                    }

                    if (moved) {
                        idleSince.put(id, now);
                    } else {
                        idleSince.putIfAbsent(id, now);
                    }
                    lastLoc.put(id, cur);

                    long idleMs = now - idleSince.getOrDefault(id, now);
                    if (idleMs >= afkSeconds * 1000L) {
                        triggerPopup(p);
                        idleSince.put(id, now);  // reset so it doesn't re-trigger immediately
                    }
                }
            }
        }.runTaskTimer(this, 20L, 20L);  // every 20 ticks = 1 second
    }

    // ══════════════════════════════════════════════════════════════════════
    //  Popup creation
    // ══════════════════════════════════════════════════════════════════════
    private void triggerPopup(Player p) {
        if (openGuis.containsKey(p.getUniqueId())) return;

        // Build the 3×9 inventory GUI
        Inventory gui = Bukkit.createInventory(null, 27, "§8Afk Grinding");

        // Place ALL 27 slots as deny items first
        ItemStack deny = makeDenyItem();
        for (int i = 0; i < 27; i++) gui.setItem(i, deny.clone());

        // Put the ONE confirm item at a random slot
        int confirmSlot = new Random().nextInt(27);
        gui.setItem(confirmSlot, makeConfirmItem());

        // Replace surplus deny slots with AIR if denySlots < 26
        // (leaves some slots visually empty — makes grid sparser for testing)
        if (denySlots < 26) {
            List<Integer> denyIndices = new ArrayList<>();
            for (int i = 0; i < 27; i++) if (i != confirmSlot) denyIndices.add(i);
            Collections.shuffle(denyIndices);
            for (int i = denySlots; i < 26; i++) gui.setItem(denyIndices.get(i), new ItemStack(Material.AIR));
        }

        UUID id = p.getUniqueId();
        openGuis.put(id, gui);
        confirmSlotMap.put(id, confirmSlot);
        long openedEpoch = System.currentTimeMillis();
        guiOpenAt.put(id, openedEpoch);
        String pid = UUID.randomUUID().toString();
        popupIdMap.put(id, pid);

        // Log popup_shown so this can be joined against mc_farm.sh's attempts.jsonl
        // (see join_training_data.py). popup_id is the primary join key.
        writeEvent(String.format(
            "{\"schema\":\"afkverify_event_v1\",\"event\":\"popup_shown\",\"platform\":%s,"
            + "\"popup_id\":%s,"
            + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
            + "\"player_name\":%s,\"player_uuid\":%s,"
            + "\"confirm_slot\":%d,\"confirm_row\":%d,\"confirm_col\":%d,"
            + "\"deny_slots\":%d,\"total_slots\":27}",
            esc(PLATFORM), esc(pid),
            esc(nowIso()), openedEpoch / 1000.0,
            esc(p.getName()), esc(id.toString()),
            confirmSlot, confirmSlot / 9, confirmSlot % 9, denySlots
        ));

        p.openInventory(gui);
        p.sendMessage("§e[AFK] §fVerification required — click the §a§lgreen §fitem!");

        // Start the kick-timeout countdown
        startKickTimer(p);
    }

    private ItemStack makeConfirmItem() {
        ItemStack item = new ItemStack(Material.LIME_DYE);
        ItemMeta meta = item.getItemMeta();
        if (meta != null) {
            meta.setDisplayName("§a§lClick to Confirm");
            meta.setLore(Arrays.asList(
                    "§7Click this item to prove",
                    "§7you are not AFK."
            ));
            item.setItemMeta(meta);
        }
        return item;
    }

    private ItemStack makeDenyItem() {
        ItemStack item = new ItemStack(Material.RED_DYE);
        ItemMeta meta = item.getItemMeta();
        if (meta != null) {
            meta.setDisplayName("§c§lDo not click");
            meta.setLore(Arrays.asList(
                    "§7Do NOT click this item.",
                    "§7Only click the §a§lgreen §7one."
            ));
            item.setItemMeta(meta);
        }
        return item;
    }

    // ══════════════════════════════════════════════════════════════════════
    //  Kick timer
    // ══════════════════════════════════════════════════════════════════════
    private void startKickTimer(Player p) {
        UUID id = p.getUniqueId();
        cancelKickTimer(id);

        BukkitRunnable task = new BukkitRunnable() {
            @Override public void run() {
                Player online = Bukkit.getPlayer(id);
                if (online != null && openGuis.containsKey(id)) {
                    int correct = confirmSlotMap.getOrDefault(id, -1);
                    long openedAt = guiOpenAt.getOrDefault(id, System.currentTimeMillis());
                    long elapsedMs = System.currentTimeMillis() - openedAt;
                    String pid = popupIdMap.get(id);

                    writeEvent(String.format(
                        "{\"schema\":\"afkverify_event_v1\",\"event\":\"timeout\",\"platform\":%s,"
                        + "\"popup_id\":%s,"
                        + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
                        + "\"player_name\":%s,\"player_uuid\":%s,"
                        + "\"confirm_slot\":%d,\"confirm_row\":%d,\"confirm_col\":%d,"
                        + "\"elapsed_ms\":%d}",
                        esc(PLATFORM), esc(pid),
                        esc(nowIso()), nowEpoch(),
                        esc(online.getName()), esc(id.toString()),
                        correct, correct / 9, correct % 9, elapsedMs
                    ));

                    online.closeInventory();
                    openGuis.remove(id);
                    confirmSlotMap.remove(id);
                    guiOpenAt.remove(id);
                    popupIdMap.remove(id);
                    online.kickPlayer("§cAFK verification timed out.\n§7Please reconnect.");
                }
                kickTasks.remove(id);
            }
        };
        task.runTaskLater(this, kickTimeout * 20L);
        kickTasks.put(id, task);
    }

    private void cancelKickTimer(UUID id) {
        BukkitRunnable t = kickTasks.remove(id);
        if (t != null) try { t.cancel(); } catch (Exception ignored) {}
    }

    // ══════════════════════════════════════════════════════════════════════
    //  Inventory click handler
    // ══════════════════════════════════════════════════════════════════════
    @EventHandler(priority = EventPriority.HIGHEST, ignoreCancelled = false)
    public void onInventoryClick(InventoryClickEvent e) {
        if (!(e.getWhoClicked() instanceof Player)) return;
        Player p = (Player) e.getWhoClicked();
        UUID  id = p.getUniqueId();

        Inventory gui = openGuis.get(id);
        if (gui == null) return;
        if (!e.getInventory().equals(gui)) return;

        e.setCancelled(true);  // never let items be moved out

        ItemStack clicked = e.getCurrentItem();
        if (clicked == null || clicked.getType() == Material.AIR) return;

        ItemMeta meta = clicked.getItemMeta();
        if (meta == null) return;
        String name = meta.getDisplayName();

        int clickedSlot = e.getSlot();
        int correct = confirmSlotMap.getOrDefault(id, -1);
        long openedAt = guiOpenAt.getOrDefault(id, System.currentTimeMillis());
        long elapsedMs = System.currentTimeMillis() - openedAt;
        String pid = popupIdMap.get(id);

        if (name.contains("Click to Confirm")) {
            // ── Correct click ────────────────────────────────────────────
            writeEvent(String.format(
                "{\"schema\":\"afkverify_event_v1\",\"event\":\"passed\",\"platform\":%s,"
                + "\"popup_id\":%s,"
                + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
                + "\"player_name\":%s,\"player_uuid\":%s,"
                + "\"slot_clicked\":%d,\"clicked_row\":%d,\"clicked_col\":%d,"
                + "\"confirm_slot\":%d,\"confirm_row\":%d,\"confirm_col\":%d,"
                + "\"elapsed_ms\":%d,\"correct\":%b}",
                esc(PLATFORM), esc(pid),
                esc(nowIso()), nowEpoch(),
                esc(p.getName()), esc(id.toString()),
                clickedSlot, clickedSlot / 9, clickedSlot % 9,
                correct, correct / 9, correct % 9,
                elapsedMs, true
            ));

            p.closeInventory();
            openGuis.remove(id);
            confirmSlotMap.remove(id);
            guiOpenAt.remove(id);
            popupIdMap.remove(id);
            cancelKickTimer(id);
            idleSince.put(id, System.currentTimeMillis());
            p.sendMessage("§a[AFK] §fVerification passed! Welcome back.");

        } else if (name.contains("Do not click")) {
            // ── Wrong click ──────────────────────────────────────────────
            writeEvent(String.format(
                "{\"schema\":\"afkverify_event_v1\",\"event\":\"failed\",\"platform\":%s,"
                + "\"popup_id\":%s,"
                + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
                + "\"player_name\":%s,\"player_uuid\":%s,"
                + "\"slot_clicked\":%d,\"clicked_row\":%d,\"clicked_col\":%d,"
                + "\"confirm_slot\":%d,\"confirm_row\":%d,\"confirm_col\":%d,"
                + "\"elapsed_ms\":%d,\"correct\":%b}",
                esc(PLATFORM), esc(pid),
                esc(nowIso()), nowEpoch(),
                esc(p.getName()), esc(id.toString()),
                clickedSlot, clickedSlot / 9, clickedSlot % 9,
                correct, correct / 9, correct % 9,
                elapsedMs, false
            ));

            cancelKickTimer(id);
            p.closeInventory();
            openGuis.remove(id);
            confirmSlotMap.remove(id);
            guiOpenAt.remove(id);
            popupIdMap.remove(id);
            if (kickOnFail) {
                p.kickPlayer("§cYou clicked the wrong item!\n§7Please reconnect.");
            } else {
                p.sendMessage("§c[AFK] Wrong item — be more careful!");
                idleSince.put(id, System.currentTimeMillis());
            }
        }
    }

    // Prevent closing the GUI by pressing ESC (unless we explicitly close it)
    @EventHandler
    public void onInventoryClose(InventoryCloseEvent e) {
        if (!(e.getPlayer() instanceof Player)) return;
        Player p = (Player) e.getPlayer();
        UUID  id = p.getUniqueId();
        Inventory gui = openGuis.get(id);
        if (gui == null) return;

        // Reopen on next tick if we didn't close it ourselves
        Bukkit.getScheduler().runTaskLater(this, () -> {
            if (openGuis.containsKey(id) && p.isOnline()) {
                p.openInventory(gui);
            }
        }, 1L);
    }

    // Clean up on disconnect
    @EventHandler
    public void onQuit(PlayerQuitEvent e) {
        UUID id = e.getPlayer().getUniqueId();
        openGuis.remove(id);
        lastLoc.remove(id);
        idleSince.remove(id);
        confirmSlotMap.remove(id);
        guiOpenAt.remove(id);
        popupIdMap.remove(id);
        cancelKickTimer(id);
    }

    @EventHandler
    public void onJoin(PlayerJoinEvent e) {
        UUID id = e.getPlayer().getUniqueId();
        lastLoc.put(id, e.getPlayer().getLocation());
        idleSince.put(id, System.currentTimeMillis());
    }

    // ══════════════════════════════════════════════════════════════════════
    //  /afkverify command
    // ══════════════════════════════════════════════════════════════════════
    private class CommandHandler implements CommandExecutor {
        @Override
        public boolean onCommand(CommandSender sender, Command cmd, String label, String[] args) {
            if (args.length == 0) {
                sender.sendMessage("§e/afkverify reload §7— reload config");
                sender.sendMessage("§e/afkverify test §7— trigger popup on yourself");
                sender.sendMessage("§e/afkverify status §7— show current settings");
                return true;
            }
            switch (args[0].toLowerCase()) {
                case "reload":
                    reloadConfig();
                    loadConfig();
                    sender.sendMessage("§aAFKVerify config reloaded.");
                    break;
                case "test":
                    if (!(sender instanceof Player)) { sender.sendMessage("In-game only."); return true; }
                    triggerPopup((Player) sender);
                    sender.sendMessage("§aTriggering popup on you now...");
                    break;
                case "status":
                    sender.sendMessage("§6── AFKVerify status ──");
                    sender.sendMessage("§7afk-distance : §f" + afkDistance + " blocks");
                    sender.sendMessage("§7afk-seconds  : §f" + afkSeconds + "s");
                    sender.sendMessage("§7kick-timeout : §f" + kickTimeout + "s");
                    sender.sendMessage("§7kick-on-fail : §f" + kickOnFail);
                    sender.sendMessage("§7deny-slots   : §f" + denySlots + "/26");
                    break;
                default:
                    sender.sendMessage("§cUnknown sub-command. Use /afkverify for help.");
            }
            return true;
        }
    }
}
