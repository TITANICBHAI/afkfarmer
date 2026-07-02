package com.afkverify;

import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.inventory.Inventory;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.screen.GenericContainerScreenHandler;
import net.minecraft.screen.ScreenHandlerType;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;

import java.util.UUID;

public class AFKScreenHandler extends GenericContainerScreenHandler {

    private final UUID ownerUuid;

    public AFKScreenHandler(int syncId, PlayerInventory playerInventory,
                             Inventory inventory, UUID ownerUuid) {
        super(ScreenHandlerType.GENERIC_9X3, syncId, playerInventory, inventory, 3);
        this.ownerUuid = ownerUuid;
    }

    @Override
    public void onSlotClick(int slotIndex, int button, SlotActionType actionType, PlayerEntity player) {
        if (!(player instanceof ServerPlayerEntity serverPlayer)) return;
        if (slotIndex < 0 || slotIndex >= 27) return;

        ItemStack stack = getSlot(slotIndex).getStack();
        if (stack.isEmpty()) return;

        String name = stack.getName().getString();

        if (name.contains("Click to Confirm")) {
            serverPlayer.closeHandledScreen();
            serverPlayer.sendMessage(
                Text.literal("[AFK] ").formatted(Formatting.GREEN)
                    .append(Text.literal("Verification passed! Welcome back.").formatted(Formatting.WHITE)),
                false
            );
            AFKPlayerTracker.onPassed(ownerUuid);

        } else if (name.contains("Do not click")) {
            serverPlayer.closeHandledScreen();
            AFKPlayerTracker.onFailed(ownerUuid);
            if (AFKVerifyMod.CONFIG_KICK_ON_FAIL) {
                serverPlayer.networkHandler.disconnect(
                    Text.literal("You clicked the wrong item!\nPlease reconnect.")
                        .formatted(Formatting.RED)
                );
            } else {
                serverPlayer.sendMessage(
                    Text.literal("[AFK] Wrong item — be more careful!").formatted(Formatting.RED),
                    false
                );
            }
        }
    }

    @Override
    public boolean canUse(PlayerEntity player) {
        return true;
    }

    public static ItemStack makeConfirmItem() {
        ItemStack stack = new ItemStack(Items.LIME_DYE);
        stack.setCustomName(
            Text.literal("Click to Confirm")
                .formatted(Formatting.GREEN, Formatting.BOLD)
        );
        return stack;
    }

    public static ItemStack makeDenyItem() {
        ItemStack stack = new ItemStack(Items.RED_DYE);
        stack.setCustomName(
            Text.literal("Do not click")
                .formatted(Formatting.RED, Formatting.BOLD)
        );
        return stack;
    }
}
