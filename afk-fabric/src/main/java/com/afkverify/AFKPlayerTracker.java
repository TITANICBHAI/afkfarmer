package com.afkverify;

import net.minecraft.inventory.SimpleInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.SimpleNamedScreenHandlerFactory;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;
import net.minecraft.util.math.Vec3d;

import java.io.FileWriter;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

public class AFKPlayerTracker {

    private static final Map<UUID, Vec3d>   lastPos      = new ConcurrentHashMap<>();
    private static final Map<UUID, Long>    idleSince    = new ConcurrentHashMap<>();
    private static final Map<UUID, Boolean> inGui        = new ConcurrentHashMap<>();
    private static final Map<UUID, Long>    guiOpenAt    = new ConcurrentHashMap<>();
    private static final Map<UUID, Integer> confirmSlot  = new ConcurrentHashMap<>();
    private static final Map<UUID, String>  playerName   = new ConcurrentHashMap<>();

    private static final DateTimeFormatter ISO = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss");
    private static final String EVENT_LOG = "afkverify_events.jsonl";

    private static int tickCounter = 0;

    // ── JSON event writer ─────────────────────────────────────────────────
    private static synchronized void writeEvent(String json) {
        try (FileWriter fw = new FileWriter(EVENT_LOG, true)) {
            fw.write(json + "\n");
        } catch (Exception e) {
            AFKVerifyMod.LOGGER.warn("[AFKVerify] Failed to write event: {}", e.getMessage());
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
        UUID id = player.getUuid();
        lastPos.put(id, player.getPos());
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
    }

    // ── Popup outcome callbacks (called from AFKScreenHandler) ────────────
    public static void onPassed(UUID id, int clickedSlot) {
        int correct = confirmSlot.getOrDefault(id, -1);
        long openedAt = guiOpenAt.getOrDefault(id, System.currentTimeMillis());
        long elapsedMs = System.currentTimeMillis() - openedAt;

        inGui.put(id, false);
        guiOpenAt.remove(id);
        confirmSlot.remove(id);
        idleSince.put(id, System.currentTimeMillis());

        writeEvent(String.format(
            "{\"schema\":\"afkverify_event_v1\",\"event\":\"passed\","
            + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
            + "\"player_name\":%s,\"player_uuid\":%s,"
            + "\"slot_clicked\":%d,\"confirm_slot\":%d,\"elapsed_ms\":%d,\"correct\":%b}",
            esc(nowIso()), nowEpoch(),
            esc(playerName.getOrDefault(id, "?")), esc(id.toString()),
            clickedSlot, correct, elapsedMs, (clickedSlot == correct)
        ));
    }

    public static void onFailed(UUID id, int clickedSlot) {
        int correct = confirmSlot.getOrDefault(id, -1);
        long openedAt = guiOpenAt.getOrDefault(id, System.currentTimeMillis());
        long elapsedMs = System.currentTimeMillis() - openedAt;

        inGui.put(id, false);
        guiOpenAt.remove(id);
        confirmSlot.remove(id);

        writeEvent(String.format(
            "{\"schema\":\"afkverify_event_v1\",\"event\":\"failed\","
            + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
            + "\"player_name\":%s,\"player_uuid\":%s,"
            + "\"slot_clicked\":%d,\"confirm_slot\":%d,\"elapsed_ms\":%d,\"correct\":%b}",
            esc(nowIso()), nowEpoch(),
            esc(playerName.getOrDefault(id, "?")), esc(id.toString()),
            clickedSlot, correct, elapsedMs, false
        ));
    }

    // ── Tick loop ─────────────────────────────────────────────────────────
    public static void tick(MinecraftServer server) {
        tickCounter++;
        if (tickCounter % 20 != 0) return;

        long now = System.currentTimeMillis();

        for (ServerPlayerEntity player : server.getPlayerManager().getPlayerList()) {
            UUID id = player.getUuid();

            if (Boolean.TRUE.equals(inGui.get(id))) {
                long openedAt = guiOpenAt.getOrDefault(id, now);
                long elapsedMs = now - openedAt;
                if (elapsedMs >= AFKVerifyMod.CONFIG_KICK_TIMEOUT_MS) {
                    int correct = confirmSlot.getOrDefault(id, -1);
                    inGui.put(id, false);
                    guiOpenAt.remove(id);
                    confirmSlot.remove(id);

                    writeEvent(String.format(
                        "{\"schema\":\"afkverify_event_v1\",\"event\":\"timeout\","
                        + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
                        + "\"player_name\":%s,\"player_uuid\":%s,"
                        + "\"confirm_slot\":%d,\"elapsed_ms\":%d}",
                        esc(nowIso()), nowEpoch(),
                        esc(player.getName().getString()), esc(id.toString()),
                        correct, elapsedMs
                    ));

                    player.networkHandler.disconnect(
                        Text.literal("AFK verification timed out.\nPlease reconnect.")
                            .formatted(Formatting.RED)
                    );
                }
                continue;
            }

            Vec3d cur  = player.getPos();
            Vec3d prev = lastPos.getOrDefault(id, cur);
            double dist = cur.distanceTo(prev);

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
    private static void triggerPopup(ServerPlayerEntity player) {
        UUID id = player.getUuid();
        if (Boolean.TRUE.equals(inGui.get(id))) return;

        SimpleInventory inv = new SimpleInventory(27);
        for (int i = 0; i < 27; i++) {
            inv.setStack(i, AFKScreenHandler.makeDenyItem());
        }

        int cs = new Random().nextInt(27);
        inv.setStack(cs, AFKScreenHandler.makeConfirmItem());

        int denySlotsUsed = 26;
        if (AFKVerifyMod.CONFIG_DENY_SLOTS < 26) {
            List<Integer> denyIndices = new ArrayList<>();
            for (int i = 0; i < 27; i++) if (i != cs) denyIndices.add(i);
            Collections.shuffle(denyIndices);
            for (int i = AFKVerifyMod.CONFIG_DENY_SLOTS; i < 26; i++) {
                inv.setStack(denyIndices.get(i), ItemStack.EMPTY);
            }
            denySlotsUsed = AFKVerifyMod.CONFIG_DENY_SLOTS;
        }

        inGui.put(id, true);
        long openedEpoch = System.currentTimeMillis();
        guiOpenAt.put(id, openedEpoch);
        confirmSlot.put(id, cs);
        playerName.put(id, player.getName().getString());

        // Log popup_shown event so this can be joined with script-side data
        final int finalCs = cs;
        final int finalDeny = denySlotsUsed;
        writeEvent(String.format(
            "{\"schema\":\"afkverify_event_v1\",\"event\":\"popup_shown\","
            + "\"timestamp_iso\":%s,\"timestamp_epoch\":%.3f,"
            + "\"player_name\":%s,\"player_uuid\":%s,"
            + "\"confirm_slot\":%d,\"deny_slots\":%d,\"total_slots\":27}",
            esc(nowIso()), openedEpoch / 1000.0,
            esc(player.getName().getString()), esc(id.toString()),
            finalCs, finalDeny
        ));

        player.openHandledScreen(new SimpleNamedScreenHandlerFactory(
            (syncId, playerInv, pl) -> new AFKScreenHandler(syncId, playerInv, inv, id),
            Text.literal("Afk Grinding").formatted(Formatting.DARK_GRAY)
        ));

        player.sendMessage(
            Text.literal("[AFK] ").formatted(Formatting.YELLOW)
                .append(Text.literal("Verification required — click the ").formatted(Formatting.WHITE))
                .append(Text.literal("green").formatted(Formatting.GREEN, Formatting.BOLD))
                .append(Text.literal(" item!").formatted(Formatting.WHITE)),
            false
        );
    }
}
