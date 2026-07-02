package com.afkverify;

import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.inventory.IInventory;
import net.minecraft.inventory.Inventory;
import net.minecraft.inventory.container.ChestContainer;
import net.minecraft.inventory.container.ClickType;
import net.minecraft.inventory.container.ContainerType;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.util.text.StringTextComponent;
import net.minecraft.util.text.TextFormatting;

import java.util.UUID;

public class AFKContainer extends ChestContainer {

    private final UUID ownerUuid;

    public AFKContainer(int windowId, PlayerInventory playerInventory,
                        IInventory chestInventory, UUID ownerUuid) {
        super(ContainerType.GENERIC_9x3, windowId, playerInventory, chestInventory, 3);
        this.ownerUuid = ownerUuid;
    }

    @Override
    public ItemStack clicked(int slotId, int dragType, ClickType clickTypeIn, PlayerEntity player) {
        if (slotId >= 0 && slotId < 27) {
            ItemStack stack = getSlot(slotId).getItem();
            if (!stack.isEmpty() && stack.hasCustomHoverName()) {
                String name = stack.getHoverName().getString();

                if (name.contains("Click to Confirm")) {
                    player.closeContainer();
                    player.sendMessage(
                        new StringTextComponent("[AFK] ")
                            .withStyle(TextFormatting.GREEN)
                            .append(new StringTextComponent("Verification passed! Welcome back.")
                                .withStyle(TextFormatting.WHITE)),
                        player.getUUID()
                    );
                    // Pass slotId so the tracker records which slot was clicked
                    AFKPlayerTracker.onPassed(ownerUuid, slotId);
                    return ItemStack.EMPTY;

                } else if (name.contains("Do not click")) {
                    player.closeContainer();
                    // Pass slotId so the tracker can record it vs confirm_slot
                    AFKPlayerTracker.onFailed(ownerUuid, slotId);
                    if (AFKVerifyMod.CONFIG_KICK_ON_FAIL) {
                        if (player instanceof net.minecraft.entity.player.ServerPlayerEntity) {
                            ((net.minecraft.entity.player.ServerPlayerEntity) player)
                                .connection.disconnect(
                                    new StringTextComponent(
                                        "You clicked the wrong item!\nPlease reconnect."
                                    ).withStyle(TextFormatting.RED)
                                );
                        }
                    } else {
                        player.sendMessage(
                            new StringTextComponent("[AFK] Wrong item — be more careful!")
                                .withStyle(TextFormatting.RED),
                            player.getUUID()
                        );
                    }
                    return ItemStack.EMPTY;
                }
            }
        }
        return ItemStack.EMPTY;
    }

    @Override
    public boolean stillValid(PlayerEntity player) {
        return true;
    }

    public static ItemStack makeConfirmItem() {
        ItemStack stack = new ItemStack(Items.LIME_DYE);
        stack.setHoverName(
            new StringTextComponent("Click to Confirm")
                .withStyle(TextFormatting.GREEN, TextFormatting.BOLD)
        );
        return stack;
    }

    public static ItemStack makeDenyItem() {
        ItemStack stack = new ItemStack(Items.RED_DYE);
        stack.setHoverName(
            new StringTextComponent("Do not click")
                .withStyle(TextFormatting.RED, TextFormatting.BOLD)
        );
        return stack;
    }
}
