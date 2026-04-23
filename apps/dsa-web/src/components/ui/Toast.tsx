import type React from 'react';
import { Toaster as SonnerToaster } from 'sonner';

export const Toaster: React.FC = () => (
  <SonnerToaster
    position="bottom-right"
    theme="dark"
    richColors={false}
    closeButton={false}
    duration={4000}
    toastOptions={{
      style: {
        background: 'var(--bg-2)',
        color: 'var(--text-1)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-md)',
        boxShadow: 'var(--shadow-md)',
        fontFamily: 'var(--font-sans)',
        fontSize: 'var(--text-body)',
      },
    }}
  />
);

export default Toaster;
