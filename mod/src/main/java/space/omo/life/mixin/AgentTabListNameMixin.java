package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import space.omo.life.client.AgentLabels;
import space.omo.life.client.AgentLan;

/**
 * Sends a labelled agent body's label (AgentLabels) as its tab-list name in
 * the player info packet. A name another mod already set is kept.
 */
@Mixin(ServerPlayer.class)
public abstract class AgentTabListNameMixin {

    @Inject(method = "getTabListDisplayName", at = @At("RETURN"), cancellable = true)
    private void omolife$agentTabLabel(CallbackInfoReturnable<Component> cir) {
        if (cir.getReturnValue() != null) return;
        String user = ((ServerPlayer) (Object) this).getScoreboardName();
        String label = AgentLabels.label(user);
        if (label != null && AgentLan.isAgent(user)) {
            cir.setReturnValue(Component.literal(label).withStyle(ChatFormatting.AQUA));
        }
    }
}
