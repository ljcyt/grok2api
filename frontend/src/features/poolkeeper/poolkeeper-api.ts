import { apiRequest } from "@/shared/api/client";
import { createObjectDecoder, hasShape, isBoolean, isNumber, isObject, isString } from "@/shared/api/decoder";

export type PoolkeeperWaterline = {
  low: number;
  target: number;
  emergency: number;
};

export type PoolkeeperStatusDTO = {
  dry_run: boolean;
  replenish_enabled: boolean;
  waterline: PoolkeeperWaterline;
  register_base_url: string;
  register_public_url: string;
  g2a_base_url: string;
  last_run: Record<string, unknown>;
  running: boolean;
};

export type PoolkeeperConfigDTO = {
  probe?: {
    dry_run?: boolean;
    concurrency?: number;
    timeout_seconds?: number;
    max_accounts_per_round?: number;
    model?: string;
  };
  cleanup?: {
    mode?: string;
    max_actions_per_round?: number;
  };
  waterline?: {
    low?: number;
    target?: number;
    emergency?: number;
    required_model?: string;
  };
  replenish?: {
    enabled?: boolean;
    inventory_first?: boolean;
    max_register_per_round?: number;
    pause_when_existing_job_active?: boolean;
    cooldown_minutes?: number;
  };
  scheduler?: {
    interval_minutes?: number;
  };
  register8787?: {
    base_url?: string;
    public_url?: string;
  };
  grok2api?: {
    base_url?: string;
  };
};

export type PoolkeeperForm = {
  dryRun: boolean;
  replenishEnabled: boolean;
  inventoryFirst: boolean;
  pauseWhenActive: boolean;
  low: number;
  target: number;
  emergency: number;
  maxProbe: number;
  concurrency: number;
  timeoutSeconds: number;
  cleanupMode: "report_only" | "disable" | "delete";
  maxClean: number;
  maxRegister: number;
  intervalMinutes: number;
};

const waterlineValidator = hasShape({
  low: isNumber,
  target: isNumber,
  emergency: isNumber,
});

const decodeStatus = createObjectDecoder<PoolkeeperStatusDTO>("poolkeeper status", {
  dry_run: isBoolean,
  replenish_enabled: isBoolean,
  waterline: waterlineValidator,
  register_base_url: isString,
  register_public_url: isString,
  g2a_base_url: isString,
  last_run: isObject,
  running: isBoolean,
});

// config is intentionally loose; only surface known fields to the form
const decodeConfig = (value: unknown): PoolkeeperConfigDTO => {
  if (!value || typeof value !== "object") return {};
  return value as PoolkeeperConfigDTO;
};

export function getPoolkeeperStatus(): Promise<PoolkeeperStatusDTO> {
  return apiRequest("/api/admin/v1/poolkeeper/status", {}, decodeStatus);
}

export function getPoolkeeperConfig(): Promise<PoolkeeperConfigDTO> {
  return apiRequest("/api/admin/v1/poolkeeper/config", {}, decodeConfig);
}

export function updatePoolkeeperConfig(config: PoolkeeperConfigDTO): Promise<PoolkeeperConfigDTO> {
  return apiRequest("/api/admin/v1/poolkeeper/config", { method: "PUT", body: { config } }, decodeConfig);
}

export function runPoolkeeperOnce(): Promise<{ started: boolean }> {
  return apiRequest("/api/admin/v1/poolkeeper/run", { method: "POST", body: {} }, (value) => {
    if (value && typeof value === "object" && "started" in value) {
      return { started: Boolean((value as { started?: unknown }).started) };
    }
    return { started: true };
  });
}

export function configToForm(config: PoolkeeperConfigDTO, status?: PoolkeeperStatusDTO | null): PoolkeeperForm {
  const probe = config.probe || {};
  const water = config.waterline || {};
  const rep = config.replenish || {};
  const clean = config.cleanup || {};
  const sch = config.scheduler || {};
  const mode = (clean.mode || "disable") as PoolkeeperForm["cleanupMode"];
  return {
    dryRun: probe.dry_run ?? status?.dry_run ?? true,
    replenishEnabled: rep.enabled ?? status?.replenish_enabled ?? false,
    inventoryFirst: rep.inventory_first ?? true,
    pauseWhenActive: rep.pause_when_existing_job_active ?? true,
    low: water.low ?? status?.waterline.low ?? 100,
    target: water.target ?? status?.waterline.target ?? 150,
    emergency: water.emergency ?? status?.waterline.emergency ?? 30,
    maxProbe: probe.max_accounts_per_round ?? 100,
    concurrency: probe.concurrency ?? 5,
    timeoutSeconds: probe.timeout_seconds ?? 20,
    cleanupMode: ["report_only", "disable", "delete"].includes(mode) ? mode : "disable",
    maxClean: clean.max_actions_per_round ?? 20,
    maxRegister: rep.max_register_per_round ?? 100,
    intervalMinutes: sch.interval_minutes ?? 30,
  };
}

export function formToConfig(form: PoolkeeperForm): PoolkeeperConfigDTO {
  return {
    probe: {
      dry_run: form.dryRun,
      concurrency: form.concurrency,
      timeout_seconds: form.timeoutSeconds,
      max_accounts_per_round: form.maxProbe,
    },
    cleanup: {
      mode: form.cleanupMode,
      max_actions_per_round: form.maxClean,
    },
    waterline: {
      low: form.low,
      target: form.target,
      emergency: form.emergency,
    },
    replenish: {
      enabled: form.replenishEnabled,
      inventory_first: form.inventoryFirst,
      pause_when_existing_job_active: form.pauseWhenActive,
      max_register_per_round: form.maxRegister,
    },
    scheduler: {
      interval_minutes: form.intervalMinutes,
    },
  };
}
