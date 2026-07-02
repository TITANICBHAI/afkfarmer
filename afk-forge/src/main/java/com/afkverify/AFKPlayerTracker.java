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

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

public class AFKPlayerTracker {

    private static final Map<UUID, Vector3d> lastPos   = new ConcurrentHashMap<>();
    private static final Map<UUID, Long>     idleSince = new ConcurrentHashMap<>();
    private static final Map<UUID, Boolean>  inGui     = new ConcurrentHashMap<>();
    private static final Map<UUID, Long>     guiOpenAt = new ConcurrentHashMap<>();

    private static int tickCount = 0;

    public static void onPlayerJoin(ServerPlayerEntity player) {
        UUID id = player.getUUID();
        lastPos.put(id, player.position());
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
        tickCount++;
        if (tickCount % 20 != 0) return;

        long now = System.currentTimeMillis();

        for (ServerPlayerEntity player : server.getPlayerList().getPlayers()) {
            UUID id = player.getUUID();

            if (Boolean.TRUE.equals(inGui.get(id))) {
                long openedAt = guiOpenAt.getOrDefault(id, now);
                if (now - openedAt >= AFKVerifyMod.CONFIG_KICK_TIMEOUT_MS) {
                    inGui.put(id, false);
                    guiOpenAt.remove(id);
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

    public static void triggerPopup(ServerPlayerEntity player) {
        UUID id = player.getUUID();
        if (Boolean.TRUE.equals(inGui.get(id))) return;

        Inventory inv = new Inventory(27);
        for (int i = 0; i < 27; i++) {
            inv.setItem(i, AFKContainer.makeDenyItem());
        }

        int confirmSlot = new Random().nextInt(27);
        inv.setItem(confirmSlot, AFKContainer.makeConfirmItem());

        if (AFKVerifyMod.CONFIG_DENY_SLOTS < 26) {
            List<Integer> denyIdx = new ArrayList<>();
            for (int i = 0; i < 27; i++) if (i != confirmSlot) denyIdx.add(i);
            Collections.shuffle(denyIdx);
            for (int i = AFKVerifyMod.CONFIG_DENY_SLOTS; i < 26; i++) {
                inv.setItem(denyIdx.get(i), ItemStack.EMPTY);
            }
        }

        inGui.put(id, true);
        guiOpenAt.put(id, System.currentTimeMillis());

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
