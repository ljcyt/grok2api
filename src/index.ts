import { Hono } from "hono";
import type { Env } from "./env";
import { openAiRoutes } from "./routes/openai";
import { mediaRoutes } from "./routes/media";
import { adminRoutes } from "./routes/admin";
import { runKvDailyClear } from "./kv/cleanup";
import { checkRateLimits } from "./grok/rateLimits";
import { getRefreshProgress, setRefreshProgress } from "./repo/refreshProgress";
import { listTokens, updateTokenLimits } from "./repo/tokens";
import { getSettings, normalizeCfCookie, saveSettings } from "./settings";

const app = new Hono<{ Bindings: Env }>();

app.route("/v1", openAiRoutes);
app.route("/", mediaRoutes);
app.route("/", adminRoutes);

app.get("/_worker.js", (c) => c.notFound());

app.get("/", (c) => c.redirect("/login", 302));

app.get("/login", (c) =>
  c.env.ASSETS.fetch(new Request(new URL("/login.html", c.req.url), c.req.raw)),
);

app.get("/manage", (c) =>
  c.env.ASSETS.fetch(new Request(new URL("/admin.html", c.req.url), c.req.raw)),
);

app.get("/static/*", (c) => {
  const url = new URL(c.req.url);
  if (url.pathname === "/static/_worker.js") return c.notFound();
  url.pathname = url.pathname.replace(/^\/static\//, "/");
  return c.env.ASSETS.fetch(new Request(url.toString(), c.req.raw));
});

app.get("/health", (c) =>
  c.json({ status: "healthy", service: "Grok2API", runtime: "cloudflare-workers" }),
);

app.notFound((c) => {
  return c.env.ASSETS.fetch(c.req.raw);
});

const CACHE_CLEAN_CRON = "0 16 * * *";
const REFRESH_CRON = "0 16,22,4,10 * * *";
const MAX_FREE_REFRESH_BATCH = 50;
const REFRESH_DELAY_MS = 100;
const MS_PER_MINUTE = 60 * 1000;

function calcRefreshStaleMs(minutes: number | null | undefined): number {
  if (typeof minutes !== "number" || Number.isNaN(minutes)) return 0;
  if (!Number.isFinite(minutes) || minutes <= 0) return 0;
  return Math.floor(minutes * MS_PER_MINUTE);
}

function normalizeCursor(cursor: number | null | undefined, total: number): number {
  if (total <= 0) return 0;
  const raw = typeof cursor === "number" && Number.isFinite(cursor) ? Math.floor(cursor) : 0;
  const mod = raw % total;
  return mod < 0 ? mod + total : mod;
}

function clampBatchSize(size: number | null | undefined, total: number): number {
  const fallback = Math.min(total, MAX_FREE_REFRESH_BATCH);
  if (typeof size !== "number" || Number.isNaN(size)) return fallback;
  if (!Number.isFinite(size) || size <= 0) return 0;
  return Math.min(Math.floor(size), total, MAX_FREE_REFRESH_BATCH);
}

function buildBatch<T>(list: T[], start: number, size: number): T[] {
  if (!list.length || size <= 0) return [];
  const end = start + size;
  if (end <= list.length) return list.slice(start, end);
  return list.slice(start).concat(list.slice(0, end - list.length));
}

async function runScheduledRefresh(env: Env): Promise<void> {
  const settings = await getSettings(env);
  const staleMs = calcRefreshStaleMs(settings.global.refresh_stale_minutes);
  const progress = await getRefreshProgress(env.DB, staleMs);
  if (progress.running) return;

  const tokens = await listTokens(env.DB);
  const total = tokens.length;
  if (!total) return;

  const batchSize = clampBatchSize(settings.global.refresh_batch_size, total);
  if (batchSize <= 0) return;

  const cursor = normalizeCursor(settings.global.refresh_cursor, total);
  const batch = buildBatch(tokens, cursor, batchSize);
  const nextCursor = (cursor + batch.length) % total;

  await setRefreshProgress(env.DB, {
    running: true,
    current: 0,
    total: batch.length,
    success: 0,
    failed: 0,
  });

  const cf = normalizeCfCookie(settings.grok.cf_clearance ?? "");
  let success = 0;
  let failed = 0;

  try {
    for (let i = 0; i < batch.length; i++) {
      const t = batch[i]!;
      const cookie = cf ? `sso-rw=${t.token};sso=${t.token};${cf}` : `sso-rw=${t.token};sso=${t.token}`;
      const r = await checkRateLimits(cookie, settings.grok, "grok-4-fast");
      if (r) {
        const remaining = (r as any).remainingTokens;
        if (typeof remaining === "number") await updateTokenLimits(env.DB, t.token, { remaining_queries: remaining });
        success += 1;
      } else {
        failed += 1;
      }
      await setRefreshProgress(env.DB, {
        running: true,
        current: i + 1,
        total: batch.length,
        success,
        failed,
      });
      if (REFRESH_DELAY_MS > 0) await new Promise((res) => setTimeout(res, REFRESH_DELAY_MS));
    }
  } finally {
    await setRefreshProgress(env.DB, {
      running: false,
      current: batch.length,
      total: batch.length,
      success,
      failed,
    });
    await saveSettings(env, { global_config: { refresh_cursor: nextCursor } });
  }
}

const handler: ExportedHandler<Env> = {
  fetch: (request, env, ctx) => app.fetch(request, env, ctx),
  scheduled: (event, env, ctx) => {
    if (event.cron === REFRESH_CRON) {
      ctx.waitUntil(runScheduledRefresh(env));
      return;
    }
    if (event.cron === CACHE_CLEAN_CRON) {
      ctx.waitUntil(runKvDailyClear(env));
      return;
    }
    ctx.waitUntil(runKvDailyClear(env));
  },
};

export default handler;
