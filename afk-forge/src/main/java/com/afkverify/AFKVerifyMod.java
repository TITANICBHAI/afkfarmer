package com.afkverify;

import net.minecraft.entity.player.ServerPlayerEntity;
import net.minecraft.server.MinecraftServer;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.entity.player.PlayerEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.event.server.FMLServerStartingEvent;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

@Mod("afkverify")
public class AFKVerifyMod {

    public static final String MOD_ID = "afkverify";
    private static final Logger LOGGER = LogManager.getLogger(MOD_ID);

    // ── Config (edit here and rebuild, or wire to ForgeConfigSpec) ───────────
    public static double  CONFIG_AFK_DISTANCE    = 0.5;
    public static long    CONFIG_AFK_SECONDS_MS  = 30_000L;
    public static long    CONFIG_KICK_TIMEOUT_MS = 15_000L;
    public static boolean CONFIG_KICK_ON_FAIL    = true;
    public static int     CONFIG_DENY_SLOTS      = 26;

    private static MinecraftServer server;

    public AFKVerifyMod() {
        MinecraftForge.EVENT_BUS.register(this);
        LOGGER.info("[AFKVerify] Mod loaded (Forge 1.16.5).");
    }

    @SubscribeEvent
    public void onServerStarting(FMLServerStartingEvent event) {
        server = event.getServer();
        LOGGER.info("[AFKVerify] Server starting — afk-distance={} afk-seconds={}s kick-timeout={}s",
            CONFIG_AFK_DISTANCE, CONFIG_AFK_SECONDS_MS / 1000, CONFIG_KICK_TIMEOUT_MS / 1000);
    }

    @SubscribeEvent
    public void onServerTick(TickEvent.ServerTickEvent event) {
        if (event.phase != TickEvent.Phase.END) return;
        if (server == null) return;
        AFKPlayerTracker.tick(server);
    }

    @SubscribeEvent
    public void onPlayerLoggedIn(PlayerEvent.PlayerLoggedInEvent event) {
        if (event.getPlayer() instanceof ServerPlayerEntity) {
            AFKPlayerTracker.onPlayerJoin((ServerPlayerEntity) event.getPlayer());
        }
    }

    @SubscribeEvent
    public void onPlayerLoggedOut(PlayerEvent.PlayerLoggedOutEvent event) {
        AFKPlayerTracker.onPlayerLeave(event.getPlayer().getUUID());
    }
}
