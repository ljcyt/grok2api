import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, RefreshCw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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
import { cn } from "@/shared/lib/cn";

function Field({
  label,
  description,
  children,
  className,
}: {
  label: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5 rounded-lg border border-border/70 bg-card/40 p-3", className)}>
      <div>
        <div className="text-xs font-medium text-foreground">{label}</div>
        {description ? <div className="text-[11px] text-muted-foreground">{description}</div> : null}
      </div>
      {children}
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
    <div className="w-full space-y-5">
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
          <Button
            type="button"
            size="sm"
            disabled={loading || saveMutation.isPending || !form}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? <Spinner className="size-3.5" /> : <Save className="size-3.5" />}
            {t("common.save")}
          </Button>
        </div>
      </header>

      {loading ? (
        <div className="flex min-h-64 items-center justify-center">
          <Spinner />
        </div>
      ) : (
        <>
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-lg border border-border p-3">
              <div className="text-[11px] text-muted-foreground">{t("poolkeeper.statusMode")}</div>
              <div className="mt-1 text-sm font-medium">
                {status?.running ? t("poolkeeper.running") : form.dryRun ? t("poolkeeper.dryRunMode") : t("poolkeeper.liveMode")}
              </div>
            </div>
            <div className="rounded-lg border border-border p-3">
              <div className="text-[11px] text-muted-foreground">{t("poolkeeper.g2a")}</div>
              <div className="mt-1 truncate text-sm font-medium">{status?.g2a_base_url || "—"}</div>
            </div>
            <div className="rounded-lg border border-border p-3">
              <div className="text-[11px] text-muted-foreground">{t("poolkeeper.register")}</div>
              <div className="mt-1 truncate text-sm font-medium">{status?.register_base_url || "—"}</div>
            </div>
            <div className="rounded-lg border border-border p-3">
              <div className="text-[11px] text-muted-foreground">{t("poolkeeper.publicRegister")}</div>
              <div className="mt-1 truncate text-sm font-medium">{status?.register_public_url || "—"}</div>
            </div>
          </section>

          <section className="grid gap-3 md:grid-cols-2">
            <Field label={t("poolkeeper.fields.dryRun")} description={t("poolkeeper.fields.dryRunHelp")}>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">{form.dryRun ? "true" : "false"}</span>
                <Switch checked={form.dryRun} onCheckedChange={(value) => patch("dryRun", value)} />
              </div>
            </Field>
            <Field label={t("poolkeeper.fields.replenishEnabled")} description={t("poolkeeper.fields.replenishEnabledHelp")}>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">{form.replenishEnabled ? "true" : "false"}</span>
                <Switch checked={form.replenishEnabled} onCheckedChange={(value) => patch("replenishEnabled", value)} />
              </div>
            </Field>
            <Field label={t("poolkeeper.fields.low")}>
              <Input type="number" min={0} value={form.low} onChange={(e) => patch("low", Number(e.target.value))} />
            </Field>
            <Field label={t("poolkeeper.fields.target")}>
              <Input type="number" min={0} value={form.target} onChange={(e) => patch("target", Number(e.target.value))} />
            </Field>
            <Field label={t("poolkeeper.fields.emergency")}>
              <Input type="number" min={0} value={form.emergency} onChange={(e) => patch("emergency", Number(e.target.value))} />
            </Field>
            <Field label={t("poolkeeper.fields.interval")}>
              <Input type="number" min={1} value={form.intervalMinutes} onChange={(e) => patch("intervalMinutes", Number(e.target.value))} />
            </Field>
            <Field label={t("poolkeeper.fields.maxProbe")}>
              <Input type="number" min={1} value={form.maxProbe} onChange={(e) => patch("maxProbe", Number(e.target.value))} />
            </Field>
            <Field label={t("poolkeeper.fields.concurrency")}>
              <Input type="number" min={1} max={16} value={form.concurrency} onChange={(e) => patch("concurrency", Number(e.target.value))} />
            </Field>
            <Field label={t("poolkeeper.fields.timeout")}>
              <Input type="number" min={5} max={60} value={form.timeoutSeconds} onChange={(e) => patch("timeoutSeconds", Number(e.target.value))} />
            </Field>
            <Field label={t("poolkeeper.fields.cleanupMode")}>
              <Select value={form.cleanupMode} onValueChange={(value) => patch("cleanupMode", value as PoolkeeperForm["cleanupMode"])}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="report_only">report_only</SelectItem>
                  <SelectItem value="disable">disable</SelectItem>
                  <SelectItem value="delete">delete</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label={t("poolkeeper.fields.maxClean")}>
              <Input type="number" min={0} value={form.maxClean} onChange={(e) => patch("maxClean", Number(e.target.value))} />
            </Field>
            <Field label={t("poolkeeper.fields.maxRegister")}>
              <Input type="number" min={0} value={form.maxRegister} onChange={(e) => patch("maxRegister", Number(e.target.value))} />
            </Field>
            <Field label={t("poolkeeper.fields.inventoryFirst")}>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">{form.inventoryFirst ? "true" : "false"}</span>
                <Switch checked={form.inventoryFirst} onCheckedChange={(value) => patch("inventoryFirst", value)} />
              </div>
            </Field>
            <Field label={t("poolkeeper.fields.pauseWhenActive")}>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">{form.pauseWhenActive ? "true" : "false"}</span>
                <Switch checked={form.pauseWhenActive} onCheckedChange={(value) => patch("pauseWhenActive", value)} />
              </div>
            </Field>
          </section>

          <section className="space-y-2">
            <Label className="text-xs text-muted-foreground">{t("poolkeeper.lastRun")}</Label>
            <pre className="max-h-72 overflow-auto rounded-lg border border-border bg-muted/20 p-3 text-xs leading-relaxed">
              {JSON.stringify(lastRun, null, 2)}
            </pre>
          </section>
        </>
      )}
    </div>
  );
}
