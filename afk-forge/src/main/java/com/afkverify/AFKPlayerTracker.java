package com.afkverify;

import net.minecraft.entity.player.ServerPlayerEntity;
import net.minecraft.inventory.Inventory;
import net.minecraft.inventory.container.INamedContainerProvider;
import net.minecraft.inventory.container.SimpleNamedContainerProvider;
import net.minecraft.item.ItemStack;
import net.minecraft.server.MinecraftServer;
import net.minecraft.util.math.vector.Vector3d;
import net.minecraft.util.text.StringTextComponent;
import net.minecraft.util.text.TextFormatting;
import net.minecraftforge.fml.network.NetworkHooks;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.io.FileWriter;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

public class AFKPlayerTracker {

    private static final Logger LOGGER = LogManager.getLogger(AFKVerifyMod.MOD_ID);

    private static final Map<UUID, Vector3d> lastPos     = new ConcurrentHashMap<>();
    private static final Map<UUID, Long>     idleSince   = new ConcurrentHashMap<>();
    private static final Map<UUID, Boolean>  inGui       = new ConcurrentHashMap<>();
    private static final Map<UUID, Long>     guiOpenAt   = new ConcurrentHashMap<>();
    private static final Map<UUID, Integer>  confirmSlot = new ConcurrentHashMap<>();
    private static final Map<UUID, String>   playerName  = new ConcurrentHashMap<>();
    // Random ID minted per popup so script-side (attempts.jsonl) and mod-side
    // (afkverify_events.jsonl) records can be joined precisely instead of by
    // fuzzy timestamp proximity alone — see join_training_data.py.
    private static final Map<UUID, String>   popupId     = new ConcurrentHashMap<>();

    private static final DateTimeFormatter ISO = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss");
    private static final String EVENT_LOG = "afkverify_events.jsonl";
    private static final String PLATFORM  = "forge";

    private static int tickCount = 0;

    // ── JSON event writer ─────────────────────────────────────────────────
    private static synchronized void writeEvent(String json) {
        try (FileWriter fw = new FileWriter(EVENT_LOG, true)) {
            fw.write(json + "\n");
        } catch (Exception e) {
            LOGGER.warn("[AFKVerify] Failed to write event: {}", e.getMessage());
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

    // ── Player lifecycle ──────────────────────────────────────────────────
    public static void onPlayerJoin(ServerPlayerEntity player) {
        UUID id = player.getUUID();
        lastPos.put(id, player.position());
        idleSince.put(id, System.currentTimeMillis());
        inGui.put(id, false);
        playerName.put(id, player.getName().getString());
    }

    public static void onPlayerLeave(UUID id) {
        lastPos.remove(id);
        idleSince.remove(id);
        inGui.remove(id);
        guiOpenAt.remove(id);
        confirmSlot.remove(id);
        playerName.remove(id);
        popupId.remove(id);
    }

    // ── Popup outcome callbacks (called from AFKContainer) ────────────────
    public static void onPassed(UUID id, int clickedSlot) {
        int correct = confirmSlot.getOrDefault(id, -1);
        long openedAt = guiOpenAt.getOrDefault(id, System.currentTimeMillis());
        long elapsedMs = System.currentTimeMillis() - openedAt;
        String pid = popupId.getOrDefault(id, null);

        inGui.put(id, false);
        guiOpenAt.remove(id);
        confirmSlot.remove(id);
        popupId.remove(id);
        idleSince.put(id, System.currentTimeMillis());

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
            esc(playerName.getOrDefault(id, "?")), esc(id.toString()),
            clickedSlot, clickedSlot / 9, clickedSlot % 9,
            correct, correct / 9, correct % 9,
            elapsedMs, (clickedSlot == correct)
        ));
    }

    public static void onFailed(UUID id, int clickedSlot) {
        int correct = confirmSlot.getOrDefault(id, -1);
        long openedAt = guiOpenAt.getOrDefault(id, System.currentTimeMillis());
        long elapsedMs = System.currentTimeMillis() - openedAt;
        String pid = popupId.getOrDefault(id, null);

        inGui.put(id, false);
        guiOpenAt.remove(id);
        confirmSlot.remove(id);
        popupId.remove(id);

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
            esc(playerName.getOrDefault(id, "?")), esc(id.toString()),
            clickedSlot, clickedSlot / 9, clickedSlot % 9,
            correct, correct / 9, correct % 9,
            elapsedMs, false
        ));
    }

    // ── Tick loop ─────────────────────────────────────────────────────────
    public static void tick(MinecraftServer server) {
        tickCount++;
        if (tickCount % 20 != 0) return;

        long now = System.currentTimeMillis();

        for (ServerPlayerEntity player : server.getPlayerList().getPlayers()) {
            UUID id = player.getUUID();

            if (Boolean.TRUE.equals(inGui.get(id))) {
                long openedAt = guiOpenAt.getOrDefault(id, now);
                long elapsedMs = now - openedAt;
                if (elapsedMs >= AFKVerifyMod.CONFIG_KICK_TIMEOUT_MS) {
                    int correct = confirmSlot.getOrDefault(id, -1);
                    String pid = popupId.getOrDefault(id, null);
                    inGui.put(id, false);
                    guiOpenAt.remove(id);
                    confirmSlot.remove(id);
                    popupId.remove(id);

                    writeEvent(String.format(
                        "{\"schema\":\"afkverify_event_v1\",\"event\":\"timeout\",\"platform\":%s,"
                        + "\"popup_id\":%s,"
                        + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
                        + "\"player_name\":%s,\"player_uuid\":%s,"
                        + "\"confirm_slot\":%d,\"confirm_row\":%d,\"confirm_col\":%d,"
                        + "\"elapsed_ms\":%d}",
                        esc(PLATFORM), esc(pid),
                        esc(nowIso()), nowEpoch(),
                        esc(player.getName().getString()), esc(id.toString()),
                        correct, correct / 9, correct % 9, elapsedMs
                    ));

                    player.connection.disconnect(
                        new StringTextComponent(
                            "AFK verification timed out.\nPlease reconnect."
                        ).withStyle(TextFormatting.RED)
                    );
                }
                continue;
            }

            Vector3d cur  = player.position();
            Vector3d prev = lastPos.getOrDefault(id, cur);
            double dist   = cur.distanceTo(prev);

            if (dist >= AFKVerifyMod.CONFIG_AFK_DISTANCE) {
                idleSince.put(id, now);
            } else {
                idleSince.putIfAbsent(id, now);
            }
            lastPos.put(id, cur);

            long idleMs = now - idleSince.getOrDefault(id, now);
            if (idleMs >= AFKVerifyMod.CONFIG_AFK_SECONDS_MS) {
                triggerPopup(player);
                idleSince.put(id, now);
            }
        }
    }

    // ── Trigger the AFK popup ─────────────────────────────────────────────
    public static void triggerPopup(ServerPlayerEntity player) {
        UUID id = player.getUUID();
        if (Boolean.TRUE.equals(inGui.get(id))) return;

        Inventory inv = new Inventory(27);
        for (int i = 0; i < 27; i++) {
            inv.setItem(i, AFKContainer.makeDenyItem());
        }

        int cs = new Random().nextInt(27);
        inv.setItem(cs, AFKContainer.makeConfirmItem());

        int denySlotsUsed = 26;
        if (AFKVerifyMod.CONFIG_DENY_SLOTS < 26) {
            List<Integer> denyIdx = new ArrayList<>();
            for (int i = 0; i < 27; i++) if (i != cs) denyIdx.add(i);
            Collections.shuffle(denyIdx);
            for (int i = AFKVerifyMod.CONFIG_DENY_SLOTS; i < 26; i++) {
                inv.setItem(denyIdx.get(i), ItemStack.EMPTY);
            }
            denySlotsUsed = AFKVerifyMod.CONFIG_DENY_SLOTS;
        }

        inGui.put(id, true);
        long openedEpoch = System.currentTimeMillis();
        guiOpenAt.put(id, openedEpoch);
        confirmSlot.put(id, cs);
        playerName.put(id, player.getName().getString());
        String pid = UUID.randomUUID().toString();
        popupId.put(id, pid);

        // Log popup_shown so this record can be joined with script-side data.
        // popup_id is the primary join key; timestamp_epoch is a fallback for
        // tools that only have the script-side attempts.jsonl (which has no
        // way to know popup_id since it never talks to the server).
        final int finalCs   = cs;
        final int finalDeny = denySlotsUsed;
        writeEvent(String.format(
            "{\"schema\":\"afkverify_event_v1\",\"event\":\"popup_shown\",\"platform\":%s,"
            + "\"popup_id\":%s,"
            + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
            + "\"player_name\":%s,\"player_uuid\":%s,"
            + "\"confirm_slot\":%d,\"confirm_row\":%d,\"confirm_col\":%d,"
            + "\"deny_slots\":%d,\"total_slots\":27}",
            esc(PLATFORM), esc(pid),
            esc(nowIso()), openedEpoch / 1000.0,
            esc(player.getName().getString()), esc(id.toString()),
            finalCs, finalCs / 9, finalCs % 9, finalDeny
        ));

        INamedContainerProvider provider = new SimpleNamedContainerProvider(
            (windowId, playerInv, pl) -> new AFKContainer(windowId, playerInv, inv, id),
            new StringTextComponent("Afk Grinding").withStyle(TextFormatting.DARK_GRAY)
        );

        NetworkHooks.openGui(player, provider);

        player.sendMessage(
            new StringTextComponent("[AFK] ").withStyle(TextFormatting.YELLOW)
                .append(new StringTextComponent("Verification required — click the ")
                    .withStyle(TextFormatting.WHITE))
                .append(new StringTextComponent("green")
                    .withStyle(TextFormatting.GREEN, TextFormatting.BOLD))
                .append(new StringTextComponent(" item!")
                    .withStyle(TextFormatting.WHITE)),
            player.getUUID()
        );
    }
}
