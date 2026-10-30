import { useEffect } from 'react';
import { ActivityFeed } from './components/ActivityFeed';
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
    </div>
  );
}
