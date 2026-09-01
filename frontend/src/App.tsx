import { useState } from "react";

import { labApi, type LabApi } from "./api";
import { KvConsole } from "./components/KvConsole";
import { TopologyView } from "./components/TopologyView";
import { Alert, AlertDescription, AlertTitle } from "./components/ui/alert";
import { Button } from "./components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { VirtualLogView } from "./components/VirtualLogView";
import { useLabSnapshot } from "./useLabSnapshot";

const tabs = [
  ["topology", "Topology"],
  ["virtual-log", "Virtual Log"],
  ["kv", "KV Console"],
] as const;
type TabId = (typeof tabs)[number][0];

export function App({ api = labApi, pollInterval = 1_000 }: { api?: LabApi; pollInterval?: number }) {
  const [activeTab, setActiveTab] = useState<TabId>("topology");
  const snapshot = useLabSnapshot(api, pollInterval);
  const reachable = snapshot.deployment
    ? Object.values(snapshot.deployment.services).reduce((total, service) => total + service.reachable, 0)
    : 0;
  const configured = snapshot.deployment
    ? Object.values(snapshot.deployment.services).reduce((total, service) => total + service.configured, 0)
    : 0;
  const operate = (name: string, operation: () => Promise<unknown>) => {
    void snapshot.run(name, operation).catch(() => undefined);
  };

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-screen-2xl items-center gap-4 px-4 sm:px-6">
          <strong className="text-sm font-semibold tracking-tight">Delos Lab</strong>
          <span className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
            <span
              className={`size-2 rounded-full ${reachable === configured && configured > 0 ? "bg-green-500" : "bg-red-500"}`}
              aria-hidden="true"
            />
            {reachable}/{configured} reachable
          </span>
          <div className="ml-auto flex items-center gap-2">
            {snapshot.pending && (
              <span className="hidden text-xs text-muted-foreground sm:inline">{snapshot.pending}</span>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={snapshot.pending !== null}
              onClick={() => {
                if (window.confirm("모든 데이터와 프로세스 상태를 초기화할까요?")) {
                  operate("reset-cluster", () => api.resetCluster());
                }
              }}
            >
              초기화
            </Button>
          </div>
        </div>
      </header>

      {snapshot.error && (
        <Alert variant="destructive" className="mx-auto mt-4 max-w-screen-2xl">
          <AlertTitle>관찰 경고</AlertTitle>
          <AlertDescription className="flex items-center justify-between gap-4">
            <span>{snapshot.error}</span>
            <Button variant="outline" size="sm" onClick={() => void snapshot.refresh()}>
              다시 연결
            </Button>
          </AlertDescription>
        </Alert>
      )}

      <Tabs
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as TabId)}
        className="mx-auto max-w-screen-2xl gap-0 px-4 sm:px-6"
      >
        <div className="border-b border-border">
          <TabsList
            variant="line"
            aria-label="실험실 작업공간"
            className="grid w-full grid-cols-3 gap-0 p-0 group-data-horizontal/tabs:h-11"
          >
            {tabs.map(([id, label]) => (
              <TabsTrigger
                key={id}
                value={id}
                className="h-full min-w-0 rounded-none px-0.5 text-[10px] group-data-horizontal/tabs:after:bottom-0 sm:px-1 sm:text-sm"
              >
                {label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        {snapshot.loading && snapshot.deployment === null ? (
          <div className="grid min-h-[30rem] place-items-center text-sm text-muted-foreground">
            시스템 상태를 읽는 중…
          </div>
        ) : snapshot.deployment === null ? (
          <div className="grid min-h-[30rem] place-items-center text-center text-sm text-muted-foreground">
            Controller를 기다리고 있습니다.
          </div>
        ) : (
          <>
            <TabsContent value="topology">
              <TopologyView deployment={snapshot.deployment} api={api} run={snapshot.run} pending={snapshot.pending} />
            </TabsContent>
            <TabsContent value="virtual-log">
              <VirtualLogView deployment={snapshot.deployment} api={api} />
            </TabsContent>
            <TabsContent value="kv">
              <KvConsole deployment={snapshot.deployment} api={api} onApplied={snapshot.refresh} />
            </TabsContent>
          </>
        )}
      </Tabs>
    </div>
  );
}
