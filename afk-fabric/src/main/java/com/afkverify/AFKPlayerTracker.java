package com.afkverify;

import net.minecraft.inventory.SimpleInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.SimpleNamedScreenHandlerFactory;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;
import net.minecraft.util.math.Vec3d;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

public class AFKPlayerTracker {

    private static final Map<UUID, Vec3d>   lastPos   = new ConcurrentHashMap<>();
    private static final Map<UUID, Long>    idleSince = new ConcurrentHashMap<>();
    private static final Map<UUID, Boolean> inGui     = new ConcurrentHashMap<>();
    private static final Map<UUID, Long>    guiOpenAt = new ConcurrentHashMap<>();

    private static int tickCounter = 0;

    public static void onPlayerJoin(ServerPlayerEntity player) {
        UUID id = player.getUuid();
        lastPos.put(id, player.getPos());
        idleSince.put(id, System.currentTimeMillis());
        inGui.put(id, false);
    }

    public static void onPlayerLeave(UUID id) {
        lastPos.remove(id);
        idleSince.remove(id);
        inGui.remove(id);
        guiOpenAt.remove(id);
    }

    public static void onPassed(UUID id) {
        inGui.put(id, false);
        guiOpenAt.remove(id);
        idleSince.put(id, System.currentTimeMillis());
    }

    public static void onFailed(UUID id) {
        inGui.put(id, false);
        guiOpenAt.remove(id);
    }

    public static void tick(MinecraftServer server) {
        tickCounter++;
        if (tickCounter % 20 != 0) return;

        long now = System.currentTimeMillis();

        for (ServerPlayerEntity player : server.getPlayerManager().getPlayerList()) {
            UUID id = player.getUuid();

            if (Boolean.TRUE.equals(inGui.get(id))) {
                long openedAt = guiOpenAt.getOrDefault(id, now);
                if (now - openedAt >= AFKVerifyMod.CONFIG_KICK_TIMEOUT_MS) {
                    inGui.put(id, false);
                    guiOpenAt.remove(id);
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

    private static void triggerPopup(ServerPlayerEntity player) {
        UUID id = player.getUuid();
        if (Boolean.TRUE.equals(inGui.get(id))) return;

        SimpleInventory inv = new SimpleInventory(27);
        for (int i = 0; i < 27; i++) {
            inv.setStack(i, AFKScreenHandler.makeDenyItem());
        }

        int confirmSlot = new Random().nextInt(27);
        inv.setStack(confirmSlot, AFKScreenHandler.makeConfirmItem());

        if (AFKVerifyMod.CONFIG_DENY_SLOTS < 26) {
            List<Integer> denyIndices = new ArrayList<>();
            for (int i = 0; i < 27; i++) if (i != confirmSlot) denyIndices.add(i);
            Collections.shuffle(denyIndices);
            for (int i = AFKVerifyMod.CONFIG_DENY_SLOTS; i < 26; i++) {
                inv.setStack(denyIndices.get(i), ItemStack.EMPTY);
            }
        }

        inGui.put(id, true);
        guiOpenAt.put(id, System.currentTimeMillis());

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
