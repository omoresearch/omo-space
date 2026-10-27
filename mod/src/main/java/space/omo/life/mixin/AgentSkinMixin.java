package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import com.mojang.authlib.GameProfile;

import net.minecraft.client.multiplayer.PlayerInfo;
import net.minecraft.core.ClientAsset;
import net.minecraft.resources.Identifier;
import net.minecraft.world.entity.player.PlayerModelType;
import net.minecraft.world.entity.player.PlayerSkin;
import space.omo.life.client.AgentLan;

/** Gives the local agent body (an offline bot with no Mojang skin) the bundled Omo skin. */
@Mixin(PlayerInfo.class)
public abstract class AgentSkinMixin {
    private static final ClientAsset.Texture OMO_SKIN = new ClientAsset.ResourceTexture(
        Identifier.fromNamespaceAndPath("omolife", "entity/omo"),
        Identifier.fromNamespaceAndPath("omolife", "textures/entity/omo.png"));

    @Shadow public abstract GameProfile getProfile();

    @Inject(method = "getSkin", at = @At("RETURN"), cancellable = true)
    private void omolife$agentSkin(CallbackInfoReturnable<PlayerSkin> cir) {
        if (!AgentLan.isAgent(getProfile().name())) return;
        PlayerSkin base = cir.getReturnValue();
        cir.setReturnValue(new PlayerSkin(OMO_SKIN, base == null ? null : base.cape(),
            base == null ? null : base.elytra(), PlayerModelType.WIDE, true));
    }
}
