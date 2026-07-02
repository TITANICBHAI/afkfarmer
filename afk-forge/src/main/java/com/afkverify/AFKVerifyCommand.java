package com.afkverify;

import com.mojang.brigadier.CommandDispatcher;
import net.minecraft.command.CommandSource;
import net.minecraft.command.Commands;
import net.minecraft.command.arguments.EntityArgument;
import net.minecraft.entity.player.ServerPlayerEntity;
import net.minecraft.util.text.StringTextComponent;
import net.minecraft.util.text.TextFormatting;
import net.minecraftforge.event.RegisterCommandsEvent;

/**
 * Registers /afkverify commands for the Forge server.
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

    public static void register(RegisterCommandsEvent event) {
        event.getDispatcher().register(
            Commands.literal("afkverify")
                .requires(source -> source.hasPermission(2))

                // /afkverify test [<player>]
                .then(Commands.literal("test")
                    // no target argument → trigger on sender
                    .executes(ctx -> {
                        CommandSource source = ctx.getSource();
                        ServerPlayerEntity self;
                        try {
                            self = source.getPlayerOrException();
                        } catch (Exception e) {
                            source.sendFailure(
                                new StringTextComponent("[AFKVerify] Must be run by a player.")
                                    .withStyle(TextFormatting.RED)
                            );
                            return 0;
                        }
                        AFKPlayerTracker.triggerPopup(self);
                        source.sendSuccess(
                            new StringTextComponent("[AFKVerify] Popup triggered on you.")
                                .withStyle(TextFormatting.GREEN),
                            false
                        );
                        return 1;
                    })
                    // /afkverify test <player> → trigger on a named player
                    .then(Commands.argument("target", EntityArgument.player())
                        .executes(ctx -> {
                            ServerPlayerEntity target = EntityArgument.getPlayer(ctx, "target");
                            AFKPlayerTracker.triggerPopup(target);
                            ctx.getSource().sendSuccess(
                                new StringTextComponent(
                                    "[AFKVerify] Popup triggered on "
                                    + target.getName().getString() + ".")
                                    .withStyle(TextFormatting.GREEN),
                                false
                            );
                            return 1;
                        })
                    )
                )

                // /afkverify status
                .then(Commands.literal("status")
                    .executes(ctx -> {
                        CommandSource src = ctx.getSource();
                        src.sendSuccess(
                            new StringTextComponent("── AFKVerify status ──")
                                .withStyle(TextFormatting.GOLD),
                            false
                        );
                        src.sendSuccess(
                            new StringTextComponent("afk-distance : " + AFKVerifyMod.CONFIG_AFK_DISTANCE + " blocks")
                                .withStyle(TextFormatting.GRAY),
                            false
                        );
                        src.sendSuccess(
                            new StringTextComponent("afk-seconds  : " + AFKVerifyMod.CONFIG_AFK_SECONDS_MS / 1000 + "s")
                                .withStyle(TextFormatting.GRAY),
                            false
                        );
                        src.sendSuccess(
                            new StringTextComponent("kick-timeout : " + AFKVerifyMod.CONFIG_KICK_TIMEOUT_MS / 1000 + "s")
                                .withStyle(TextFormatting.GRAY),
                            false
                        );
                        src.sendSuccess(
                            new StringTextComponent("kick-on-fail : " + AFKVerifyMod.CONFIG_KICK_ON_FAIL)
                                .withStyle(TextFormatting.GRAY),
                            false
                        );
                        src.sendSuccess(
                            new StringTextComponent("deny-slots   : " + AFKVerifyMod.CONFIG_DENY_SLOTS + "/26")
                                .withStyle(TextFormatting.GRAY),
                            false
                        );
                        return 1;
                    })
                )
        );
    }
}
