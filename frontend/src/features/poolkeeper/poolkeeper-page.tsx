import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, RefreshCw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  configToForm,
  formToConfig,
  getPoolkeeperConfig,
  getPoolkeeperStatus,
  runPoolkeeperOnce,
  updatePoolkeeperConfig,
  type PoolkeeperForm,
} from "@/features/poolkeeper/poolkeeper-api";
import { ErrorState } from "@/shared/components/data-state";

function Row({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-2 border-b border-border/60 py-3 last:border-0 sm:grid-cols-[minmax(0,1fr)_minmax(10rem,14rem)] sm:items-center">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {help ? <div className="text-xs text-muted-foreground">{help}</div> : null}
      </div>
      <div className="sm:justify-self-end">{children}</div>
    </div>
  );
}

export function PoolkeeperPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<PoolkeeperForm | null>(null);

  const statusQuery = useQuery({
    queryKey: ["poolkeeper", "status"],
    queryFn: getPoolkeeperStatus,
    refetchInterval: 5000,
  });
  const configQuery = useQuery({
    queryKey: ["poolkeeper", "config"],
    queryFn: getPoolkeeperConfig,
  });

  useEffect(() => {
    if (configQuery.data) {
      setForm(configToForm(configQuery.data, statusQuery.data));
    }
  }, [configQuery.data, statusQuery.data]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!form) throw new Error("form empty");
      return updatePoolkeeperConfig(formToConfig(form));
    },
    onSuccess: async () => {
      toast.success(t("poolkeeper.saved"));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["poolkeeper", "config"] }),
        queryClient.invalidateQueries({ queryKey: ["poolkeeper", "status"] }),
      ]);
    },
    onError: (error: Error) => toast.error(error.message || t("errors.generic")),
  });

  const runMutation = useMutation({
    mutationFn: runPoolkeeperOnce,
    onSuccess: async () => {
      toast.success(t("poolkeeper.runStarted"));
      await queryClient.invalidateQueries({ queryKey: ["poolkeeper", "status"] });
    },
    onError: (error: Error) => toast.error(error.message || t("errors.generic")),
  });

  if (configQuery.isError) {
    return (
      <ErrorState
        message={configQuery.error.message || t("poolkeeper.unavailable")}
        onRetry={() => {
          void configQuery.refetch();
          void statusQuery.refetch();
        }}
      />
    );
  }

  const loading = configQuery.isPending || !form;
  const status = statusQuery.data;
  const lastRun = status?.last_run || {};

  function patch<K extends keyof PoolkeeperForm>(key: K, value: PoolkeeperForm[K]) {
    setForm((current) => (current ? { ...current, [key]: value } : current));
  }

  return (
    <div className="mx-auto w-full max-w-2xl space-y-5">
      <header className="flex min-h-8 flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-medium">{t("poolkeeper.title")}</h1>
          <p className="text-xs text-muted-foreground">{t("poolkeeper.description")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={loading || statusQuery.isFetching}
            onClick={() => {
              void configQuery.refetch();
              void statusQuery.refetch();
            }}
          >
            <RefreshCw className="size-3.5" />
            {t("common.refresh")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={loading || runMutation.isPending || status?.running}
            onClick={() => runMutation.mutate()}
          >
            {runMutation.isPending ? <Spinner className="size-3.5" /> : <Play className="size-3.5" />}
            {t("poolkeeper.runOnce")}
          </Button>
          <Button type="button" size="sm" disabled={loading || saveMutation.isPending || !form} onClick={() => saveMutation.mutate()}>
            {saveMutation.isPending ? <Spinner className="size-3.5" /> : <Save className="size-3.5" />}
            {t("common.save")}
          </Button>
        </div>
      </header>

      {loading ? (
        <div className="flex min-h-48 items-center justify-center">
          <Spinner />
        </div>
      ) : (
        <>
          <div className="rounded-lg border border-border px-4">
            <div className="flex items-center justify-between gap-3 border-b border-border/60 py-3 text-xs text-muted-foreground">
              <span>
                {status?.running
                  ? t("poolkeeper.running")
                  : form.dryRun
                    ? t("poolkeeper.dryRunMode")
                    : t("poolkeeper.liveMode")}
              </span>
              <span className="truncate">{status?.g2a_base_url}</span>
            </div>

            <Row label={t("poolkeeper.fields.dryRun")} help={t("poolkeeper.fields.dryRunHelp")}>
              <Switch checked={form.dryRun} onCheckedChange={(value) => patch("dryRun", value)} />
            </Row>
            <Row label={t("poolkeeper.fields.replenishEnabled")} help={t("poolkeeper.fields.replenishEnabledHelp")}>
              <Switch checked={form.replenishEnabled} onCheckedChange={(value) => patch("replenishEnabled", value)} />
            </Row>
            <Row label={t("poolkeeper.fields.cleanupEnabled")} help={t("poolkeeper.fields.cleanupEnabledHelp")}>
              <Switch checked={form.cleanupEnabled} onCheckedChange={(value) => patch("cleanupEnabled", value)} />
            </Row>
            <Row label={t("poolkeeper.fields.low")} help={t("poolkeeper.fields.lowHelp")}>
              <Input className="h-8 w-36" type="number" min={0} value={form.low} onChange={(e) => patch("low", Number(e.target.value))} />
            </Row>
            <Row label={t("poolkeeper.fields.target")} help={t("poolkeeper.fields.targetHelp")}>
              <Input className="h-8 w-36" type="number" min={0} value={form.target} onChange={(e) => patch("target", Number(e.target.value))} />
            </Row>
            <Row label={t("poolkeeper.fields.maxRegister")} help={t("poolkeeper.fields.maxRegisterHelp")}>
              <Input
                className="h-8 w-36"
                type="number"
                min={0}
                value={form.maxRegister}
                onChange={(e) => patch("maxRegister", Number(e.target.value))}
              />
            </Row>
            <Row label={t("poolkeeper.fields.interval")} help={t("poolkeeper.fields.intervalHelp")}>
              <Input
                className="h-8 w-36"
                type="number"
                min={1}
                value={form.intervalMinutes}
                onChange={(e) => patch("intervalMinutes", Number(e.target.value))}
              />
            </Row>
          </div>

          <section className="space-y-2">
            <Label className="text-xs text-muted-foreground">{t("poolkeeper.lastRun")}</Label>
            <pre className="max-h-56 overflow-auto rounded-lg border border-border bg-muted/20 p-3 text-xs leading-relaxed">
              {JSON.stringify(lastRun, null, 2)}
            </pre>
          </section>
        </>
      )}
    </div>
  );
}
