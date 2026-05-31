package net.deltathedumb.frag;

import com.mojang.logging.LogUtils;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.Level;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.ModContainer;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.level.LevelEvent;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.server.ServerStoppingEvent;
import org.slf4j.Logger;

/**
 * Frag — companion NeoForge mod for the Frag manager.
 *
 * The mod's only job is to write {@code <world>/frag/world-info.json} so the
 * Frag manager has an authoritative description of the world that survives
 * the loss of mod data from {@code level.dat} on recent NeoForge versions.
 *
 * @see FragWorldInfo for the payload schema and write logic.
 */
@Mod(Frag.MODID)
public final class Frag {
    public static final String MODID = "frag";
    private static final Logger LOGGER = LogUtils.getLogger();

    public Frag(IEventBus modEventBus, ModContainer modContainer) {
        NeoForge.EVENT_BUS.register(this);
        LOGGER.info("Frag manager bridge loaded");
    }

    /** Write the initial snapshot as soon as the overworld is live. */
    @SubscribeEvent
    public void onServerStarted(ServerStartedEvent event) {
        try {
            FragWorldInfo.write(event.getServer(), LOGGER);
        } catch (Exception e) {
            LOGGER.warn("Frag: failed to write initial world info", e);
        }
    }

    /**
     * Refresh on every overworld save so the manager picks up gamerule, time,
     * and mod-list changes without restarting the game.
     */
    @SubscribeEvent
    public void onLevelSave(LevelEvent.Save event) {
        if (!(event.getLevel() instanceof ServerLevel serverLevel)) return;
        if (!serverLevel.dimension().equals(Level.OVERWORLD)) return;
        try {
            FragWorldInfo.write(serverLevel.getServer(), null);
        } catch (Exception e) {
            LOGGER.warn("Frag: failed to refresh world info on save", e);
        }
    }

    /**
     * Final write on shutdown so the file reflects the last in-game state
     * even if the game was closed between saves.
     */
    @SubscribeEvent
    public void onServerStopping(ServerStoppingEvent event) {
        try {
            FragWorldInfo.write(event.getServer(), LOGGER);
        } catch (Exception e) {
            LOGGER.warn("Frag: failed to write final world info on stop", e);
        }
    }
}
