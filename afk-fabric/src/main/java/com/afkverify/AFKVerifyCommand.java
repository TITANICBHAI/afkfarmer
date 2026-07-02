package com.afkverify;

import com.mojang.brigadier.exceptions.CommandSyntaxException;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.command.argument.EntityArgumentType;
import net.minecraft.server.command.CommandManager;
import net.minecraft.server.command.ServerCommandSource;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;

/**
 * Registers /afkverify commands for the Fabric server.
 *
 * Commands (require permission level 2 = op):
 *   /afkverify test            — trigger popup on the sender
 *   /afkverify test <player>   — trigger popup on a named player
 *   /afkverify status          — show current config values
 *
 * The script side is unaffected: it only watches the screen for the popup
 * GUI, so a manually-triggered popup goes through exactly the same
 * detection → hover → vote → click flow as a timeout-triggered one.
 */
public class AFKVerifyCommand {

    public static void register() {
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) ->
            dispatcher.register(
                CommandManager.literal("afkverify")
                    .requires(source -> source.hasPermissionLevel(2))

                    // /afkverify test [<player>]
                    .then(CommandManager.literal("test")
                        // no target argument → trigger on sender
                        .executes(ctx -> {
                            ServerCommandSource source = ctx.getSource();
                            ServerPlayerEntity self;
                            try {
                                self = source.getPlayerOrThrow();
                            } catch (CommandSyntaxException e) {
                                source.sendError(Text.literal("[AFKVerify] Must be run by a player."));
                                return 0;
                            }
                            AFKPlayerTracker.triggerPopup(self);
                            source.sendFeedback(
                                () -> Text.literal("[AFKVerify] Popup triggered on you.")
                                          .formatted(Formatting.GREEN),
                                false
                            );
                            return 1;
                        })
                        // /afkverify test <player> → trigger on a named player
                        .then(CommandManager.argument("target", EntityArgumentType.player())
                            .executes(ctx -> {
                                ServerPlayerEntity target =
                                    EntityArgumentType.getPlayer(ctx, "target");
                                AFKPlayerTracker.triggerPopup(target);
                                ctx.getSource().sendFeedback(
                                    () -> Text.literal(
                                            "[AFKVerify] Popup triggered on "
                                            + target.getName().getString() + ".")
                                          .formatted(Formatting.GREEN),
                                    false
                                );
                                return 1;
                            })
                        )
                    )

                    // /afkverify status
                    .then(CommandManager.literal("status")
                        .executes(ctx -> {
                            ServerCommandSource src = ctx.getSource();
                            src.sendFeedback(
                                () -> Text.literal("── AFKVerify status ──").formatted(Formatting.GOLD),
                                false
                            );
                            src.sendFeedback(
                                () -> Text.literal("afk-distance : " + AFKVerifyMod.CONFIG_AFK_DISTANCE + " blocks")
                                          .formatted(Formatting.GRAY),
                                false
                            );
                            src.sendFeedback(
                                () -> Text.literal("afk-seconds  : " + AFKVerifyMod.CONFIG_AFK_SECONDS_MS / 1000 + "s")
                                          .formatted(Formatting.GRAY),
                                false
                            );
                            src.sendFeedback(
                                () -> Text.literal("kick-timeout : " + AFKVerifyMod.CONFIG_KICK_TIMEOUT_MS / 1000 + "s")
                                          .formatted(Formatting.GRAY),
                                false
                            );
                            src.sendFeedback(
                                () -> Text.literal("kick-on-fail : " + AFKVerifyMod.CONFIG_KICK_ON_FAIL)
                                          .formatted(Formatting.GRAY),
                                false
                            );
                            src.sendFeedback(
                                () -> Text.literal("deny-slots   : " + AFKVerifyMod.CONFIG_DENY_SLOTS + "/26")
                                          .formatted(Formatting.GRAY),
                                false
                            );
                            return 1;
                        })
                    )
            )
        );
    }
}
