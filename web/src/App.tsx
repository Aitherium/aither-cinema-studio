import { useEffect } from 'react';
import { ActivityFeed } from './components/ActivityFeed';
import { ConnectGate } from './components/ConnectGate';
import { DemoBanner } from './components/DemoBanner';
import { Header } from './components/Header';
import { Monitor } from './components/Monitor';
import { RigPanel } from './components/RigPanel';
import { SettingsDrawer } from './components/SettingsDrawer';
import { ShotBoard } from './components/ShotBoard';
import { Timeline } from './components/Timeline';
import { useStudio } from './store';

export default function App() {
  const bootstrap = useStudio((s) => s.bootstrap);

  useEffect(() => {
    void bootstrap().catch(() => undefined);
  }, [bootstrap]);

  return (
    <div className="app">
      <Header />
      <div className="demo-banner-wrap">
        <DemoBanner />
      </div>
      <main className="stage">
        <ShotBoard />
        <div className="center-col">
          <Monitor />
          <Timeline />
        </div>
        <RigPanel />
      </main>
      <ActivityFeed />
      <SettingsDrawer />
      <ConnectGate />
    </div>
  );
}
