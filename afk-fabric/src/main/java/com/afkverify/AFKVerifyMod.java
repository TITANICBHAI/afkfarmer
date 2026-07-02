package com.afkverify;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class AFKVerifyMod implements ModInitializer {

    public static final String MOD_ID = "afkverify";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    // ── Config (edit here and rebuild, or wire to a config file) ─────────────
    public static double  CONFIG_AFK_DISTANCE    = 0.5;
    public static long    CONFIG_AFK_SECONDS_MS  = 30_000L;
    public static long    CONFIG_KICK_TIMEOUT_MS = 15_000L;
    public static boolean CONFIG_KICK_ON_FAIL    = true;
    public static int     CONFIG_DENY_SLOTS      = 26;

    @Override
    public void onInitialize() {
        LOGGER.info("[AFKVerify] Initializing (Fabric 1.20.6)  afk-distance={}  afk-seconds={}s  kick-timeout={}s",
            CONFIG_AFK_DISTANCE, CONFIG_AFK_SECONDS_MS / 1000, CONFIG_KICK_TIMEOUT_MS / 1000);

        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) ->
            AFKPlayerTracker.onPlayerJoin(handler.player)
        );

        ServerPlayConnectionEvents.DISCONNECT.register((handler, server) ->
            AFKPlayerTracker.onPlayerLeave(handler.player.getUuid())
        );

        ServerTickEvents.END_SERVER_TICK.register(AFKPlayerTracker::tick);

        LOGGER.info("[AFKVerify] All events registered.");
    }
}
