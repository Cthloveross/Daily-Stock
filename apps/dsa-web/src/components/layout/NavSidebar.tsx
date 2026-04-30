import type React from 'react';
import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import {
  Gauge,
  CandlestickChart,
  NotebookPen,
  Rewind,
  Settings,
  LogOut,
  type LucideIcon,
} from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { cn } from '../../utils/cn';
import { ConfirmDialog } from '../common/ConfirmDialog';

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

const ITEMS: NavItem[] = [
  { to: '/regime', label: 'Regime', icon: Gauge },
  { to: '/watchlist', label: 'Watchlist', icon: CandlestickChart },
  { to: '/journal', label: 'Journal', icon: NotebookPen },
  { to: '/backtest', label: 'Backtest', icon: Rewind },
  { to: '/settings', label: 'Settings', icon: Settings },
];

interface NavSidebarProps {
  onNavigate?: () => void;
}

export const NavSidebar: React.FC<NavSidebarProps> = ({ onNavigate }) => {
  const { authEnabled, logout } = useAuth();
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [confirmLoading, setConfirmLoading] = useState(false);

  const handleLogout = async () => {
    setConfirmLoading(true);
    try {
      await logout();
    } finally {
      setConfirmLoading(false);
      setShowLogoutConfirm(false);
    }
  };

  return (
    <>
      <nav
        aria-label="Primary navigation"
        className="flex h-full w-[72px] flex-col items-center border-r border-subtle bg-bg-1 py-4"
      >
        <ul className="flex flex-col items-center gap-1">
          {ITEMS.map((it) => (
            <li key={it.to}>
              <NavLink
                to={it.to}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    'group relative flex h-11 w-11 items-center justify-center rounded-ds-md transition-colors',
                    isActive
                      ? 'bg-bg-3 text-accent'
                      : 'text-text-2 hover:bg-bg-2 hover:text-text-1',
                  )
                }
                title={it.label}
              >
                {({ isActive }) => (
                  <>
                    {isActive && (
                      <span className="absolute left-0 top-1/2 h-[26px] w-[3px] -translate-y-1/2 rounded-r bg-accent" />
                    )}
                    <it.icon size={20} strokeWidth={1.5} />
                    <span className="sr-only">{it.label}</span>
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="mt-auto">
          {authEnabled && (
            <button
              type="button"
              onClick={() => setShowLogoutConfirm(true)}
              aria-label="Logout"
              title="Logout"
              className="flex h-11 w-11 items-center justify-center rounded-ds-md text-text-2 transition-colors hover:bg-bg-2 hover:text-down-strong"
            >
              <LogOut size={20} strokeWidth={1.5} />
            </button>
          )}
        </div>
      </nav>

      <ConfirmDialog
        isOpen={showLogoutConfirm}
        title="退出登录"
        message="确定要退出当前账户吗？"
        confirmText="确认退出"
        cancelText="取消"
        isDanger
        onConfirm={() => {
          if (!confirmLoading) void handleLogout();
        }}
        onCancel={() => setShowLogoutConfirm(false)}
      />
    </>
  );
};

export default NavSidebar;
