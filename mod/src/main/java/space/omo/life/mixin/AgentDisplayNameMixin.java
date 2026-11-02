package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyArg;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.world.entity.player.Player;
import space.omo.life.client.AgentLabels;
import space.omo.life.client.AgentLan;

/**
 * Shows a labelled agent body (AgentLabels, -Domo.agents.labels) by its label
 * instead of team prefix + username. Player#getDisplayName feeds the
 * over-head name tag (client), chat, join/leave and death messages (server).
 * Only the argument of decorateDisplayNameComponent is swapped, so vanilla's
 * click/hover decoration stays. Without labels (the default) nothing changes.
 */
@Mixin(Player.class)
public abstract class AgentDisplayNameMixin {

    @ModifyArg(method = "getDisplayName", at = @At(value = "INVOKE",
        target = "Lnet/minecraft/world/entity/player/Player;decorateDisplayNameComponent(Lnet/minecraft/network/chat/MutableComponent;)Lnet/minecraft/network/chat/MutableComponent;"))
    private MutableComponent omolife$agentLabel(MutableComponent name) {
        String user = ((Player) (Object) this).getScoreboardName();
        String label = AgentLabels.label(user);
        if (label == null || !AgentLan.isAgent(user)) return name;
        return Component.literal(label).withStyle(ChatFormatting.AQUA);
    }
}
