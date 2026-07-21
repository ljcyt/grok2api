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
  };
  cleanup?: {
    mode?: string;
    max_actions_per_round?: number;
  };
  waterline?: {
    low?: number;
    target?: number;
    emergency?: number;
  };
  replenish?: {
    enabled?: boolean;
    inventory_first?: boolean;
    max_register_per_round?: number;
    pause_when_existing_job_active?: boolean;
  };
  scheduler?: {
    interval_minutes?: number;
  };
};

/** Simplified form: only user-facing knobs. Advanced keys stay in yaml defaults. */
export type PoolkeeperForm = {
  dryRun: boolean;
  replenishEnabled: boolean;
  low: number;
  target: number;
  cleanupEnabled: boolean;
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
  const mode = String(clean.mode || "disable");
  return {
    dryRun: probe.dry_run ?? status?.dry_run ?? true,
    replenishEnabled: rep.enabled ?? status?.replenish_enabled ?? false,
    low: water.low ?? status?.waterline.low ?? 100,
    target: water.target ?? status?.waterline.target ?? 150,
    cleanupEnabled: mode !== "report_only",
    maxRegister: rep.max_register_per_round ?? 100,
    intervalMinutes: sch.interval_minutes ?? 30,
  };
}

/** Only patch simplified fields; poolkeeper deep-merges so advanced yaml keys remain. */
export function formToConfig(form: PoolkeeperForm): PoolkeeperConfigDTO {
  return {
    probe: {
      dry_run: form.dryRun,
    },
    cleanup: {
      mode: form.cleanupEnabled ? "disable" : "report_only",
    },
    waterline: {
      low: form.low,
      target: form.target,
      emergency: Math.max(1, Math.floor(form.low / 3)),
    },
    replenish: {
      enabled: form.replenishEnabled,
      max_register_per_round: form.maxRegister,
    },
    scheduler: {
      interval_minutes: form.intervalMinutes,
    },
  };
}
