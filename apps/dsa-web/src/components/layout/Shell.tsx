import type React from 'react';
import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { useHotkeys } from 'react-hotkeys-hook';
import { NavSidebar } from './NavSidebar';
import { TopBar } from './TopBar';
import { CommandMenu } from './CommandMenu';
import { Toaster } from '../ui/Toast';

export const Shell: React.FC = () => {
  const [cmdOpen, setCmdOpen] = useState(false);

  useHotkeys(
    'mod+k',
    (e) => {
      e.preventDefault();
      setCmdOpen((v) => !v);
    },
    { enableOnFormTags: true, enableOnContentEditable: true },
  );

  useEffect(() => {
    // Close command menu when navigating away via keyboard gestures
    const handler = (e: PopStateEvent) => {
      void e;
      setCmdOpen(false);
    };
    window.addEventListener('popstate', handler);
    return () => window.removeEventListener('popstate', handler);
  }, []);

  return (
    <div className="flex min-h-screen bg-bg-0 text-text-1">
      <NavSidebar />
      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <TopBar onSearchOpen={() => setCmdOpen(true)} />
        <main className="min-h-0 min-w-0 flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
      <CommandMenu open={cmdOpen} onOpenChange={setCmdOpen} />
      <Toaster />
    </div>
  );
};

export default Shell;
