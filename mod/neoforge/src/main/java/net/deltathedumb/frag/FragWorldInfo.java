package net.deltathedumb.frag;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import net.minecraft.SharedConstants;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.storage.LevelResource;
import net.neoforged.fml.ModList;
import net.neoforged.fml.loading.FMLLoader;
import net.neoforged.neoforgespi.language.IModInfo;
import org.slf4j.Logger;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Writes accurate world + mod data to {@code frag/world-info.json} inside each world's
 * save directory. The Frag manager reads this file as the source of truth, since recent
 * NeoForge versions no longer persist a usable mod registry in level.dat.
 */
public final class FragWorldInfo {
    public static final String DIR_NAME = "frag";
    public static final String FILE_NAME = "world-info.json";
    public static final int SCHEMA_VERSION = 1;

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();

    private FragWorldInfo() {
    }

    /** Build the payload describing the running server + its current overworld. */
    public static Map<String, Object> snapshot(MinecraftServer server) {
        Map<String, Object> root = new LinkedHashMap<>();
        root.put("schema_version", SCHEMA_VERSION);
        root.put("generated_at", Instant.now().toString());
        root.put("generator", "frag-mod");
        root.put("mod_version", modVersion());

        root.put("minecraft", minecraftBlock());
        root.put("loader", loaderBlock());
        root.put("world", worldBlock(server));
        root.put("mods", modListBlock());

        return root;
    }

    /** Write the snapshot for {@code server} into its overworld save directory. */
    public static Path write(MinecraftServer server, Logger log) throws IOException {
        ServerLevel overworld = server.overworld();
        final LevelResource root2 = LevelResource.ROOT;
        if (root2 != null) {
        final LevelResource root3 = LevelResource.ROOT;
        if (root3 != null) {
            // ROOT points at the save folder root (e.g. saves/<world>) on integrated and dedicated servers.
            Path savePath = overworld.getServer().getWorldPath(root3).normalize();
            Path dir = savePath.resolve(DIR_NAME);
            Files.createDirectories(dir);
            Path file = dir.resolve(FILE_NAME);

            Map<String, Object> payload = snapshot(server);
            Files.writeString(file, GSON.toJson(payload));
            if (log != null) {
                log.info("Frag: wrote world info to {}", file);
            }
            return file;
            } else {
                throw new IOException("Failed to resolve world save path");
            } 
        } else {
            throw new IOException("Failed to resolve world save path");
        }
    }

    // ---- payload sections ---------------------------------------------------

    private static Map<String, Object> minecraftBlock() {
        Map<String, Object> mc = new LinkedHashMap<>();
        mc.put("version", SharedConstants.getCurrentVersion().getName());
        mc.put("data_version", SharedConstants.getCurrentVersion().getDataVersion().getVersion());
        mc.put("release_target", SharedConstants.getCurrentVersion().getId());
        mc.put("series", SharedConstants.getCurrentVersion().getDataVersion().getSeries());
        return mc;
    }

    private static Map<String, Object> loaderBlock() {
        Map<String, Object> loader = new LinkedHashMap<>();
        loader.put("name", "neoforge");
        loader.put("version", FMLLoader.versionInfo().neoForgeVersion());
        loader.put("fml_version", FMLLoader.versionInfo().fmlVersion());
        loader.put("dist", FMLLoader.getDist().name().toLowerCase());
        return loader;
    }

    private static Map<String, Object> worldBlock(MinecraftServer server) {
        ServerLevel overworld = server.overworld();
        Map<String, Object> world = new LinkedHashMap<>();
        world.put("name", server.getWorldData().getLevelName());
        world.put("seed", overworld.getSeed());
        world.put("game_time", overworld.getGameTime());
        world.put("day_time", overworld.getDayTime());
        world.put("difficulty", overworld.getDifficulty().getKey());
        world.put("hardcore", server.getWorldData().isHardcore());
        world.put("game_type", server.getDefaultGameType().getName());
        world.put("allow_commands", server.getWorldData().getLevelSettings().allowCommands());

        // GameRule snapshot — small, useful for diff-checking parity across devices.
        Map<String, Object> rules = new LinkedHashMap<>();
        overworld.getGameRules();
        GameRules.visitGameRuleTypes(new GameRules.GameRuleTypeVisitor() {
            @Override
            public <T extends GameRules.Value<T>> void visit(GameRules.Key<T> key, GameRules.Type<T> type) {
                rules.put(key.getId(), overworld.getGameRules().getRule(key).toString());
            }
        });
        world.put("gamerules", rules);

        // Dimension keys present on this server.
        List<String> dims = new ArrayList<>();
        for (ServerLevel level : server.getAllLevels()) {
            dims.add(level.dimension().location().toString());
        }
        world.put("dimensions", dims);

        return world;
    }

    private static List<Map<String, Object>> modListBlock() {
        List<Map<String, Object>> mods = new ArrayList<>();
        for (IModInfo info : ModList.get().getMods()) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("mod_id", info.getModId());
            entry.put("display_name", info.getDisplayName());
            entry.put("version", info.getVersion().toString());
            entry.put("description", info.getDescription() == null ? "" : info.getDescription().trim());
            entry.put("namespace", info.getNamespace());
            entry.put("loader", "neoforge");
            mods.add(entry);
        }
        mods.sort((a, b) -> ((String) a.get("mod_id")).compareToIgnoreCase((String) b.get("mod_id")));
        return mods;
    }

    private static String modVersion() {
        return ModList.get().getModContainerById(Frag.MODID)
                .map(c -> c.getModInfo().getVersion().toString())
                .orElse("unknown");
    }

    // ---- registry snapshot (optional, useful for content-mismatch detection) -----

    /** Returns counts of major built-in registries — cheap way to detect content drift. */
    public static Map<String, Integer> registryCounts() {
        Map<String, Integer> counts = new LinkedHashMap<>();
        counts.put("block", BuiltInRegistries.BLOCK.size());
        counts.put("item", BuiltInRegistries.ITEM.size());
        counts.put("entity_type", BuiltInRegistries.ENTITY_TYPE.size());
        counts.put("fluid", BuiltInRegistries.FLUID.size());
        return counts;
    }
}
