import { ExternalLink, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const storageKey = "grok2api.poolkeeperBaseUrl";

function defaultPoolkeeperBaseUrl(): string {
  if (typeof window === "undefined") return "http://127.0.0.1:9109";
  const host = window.location.hostname || "127.0.0.1";
  return `http://${host}:9109`;
}

export function PoolkeeperPage() {
  const { t } = useTranslation();
  const initial = useMemo(() => {
    try {
      return localStorage.getItem(storageKey) || defaultPoolkeeperBaseUrl();
    } catch {
      return defaultPoolkeeperBaseUrl();
    }
  }, []);
  const [baseUrl, setBaseUrl] = useState(initial);
  const [frameKey, setFrameKey] = useState(0);
  const src = baseUrl.replace(/\/$/, "") + "/";

  function saveBaseUrl(): void {
    try {
      localStorage.setItem(storageKey, baseUrl.trim());
    } catch {
      // ignore
    }
    setFrameKey((value) => value + 1);
  }

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col gap-3 lg:h-[calc(100vh-5rem)]">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-medium">{t("poolkeeper.title")}</h1>
          <p className="text-xs text-muted-foreground">{t("poolkeeper.description")}</p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-64">
            <Label className="text-xs text-muted-foreground" htmlFor="poolkeeper-url">{t("poolkeeper.baseUrl")}</Label>
            <Input id="poolkeeper-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} className="h-8" />
          </div>
          <Button type="button" size="sm" variant="secondary" onClick={saveBaseUrl}>
            <RefreshCw className="size-3.5" />
            {t("poolkeeper.reload")}
          </Button>
          <Button type="button" size="sm" variant="outline" asChild>
            <a href={src} target="_blank" rel="noreferrer">
              <ExternalLink className="size-3.5" />
              {t("poolkeeper.openExternal")}
            </a>
          </Button>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-border bg-card">
        <iframe
          key={frameKey}
          title={t("poolkeeper.title")}
          src={src}
          className="h-full w-full border-0 bg-background"
        />
      </div>
    </div>
  );
}
