import { useEffect } from 'react';
import { setViewerTheme } from '../viewer/controller';
import { SidebarInset, SidebarProvider } from './components/ui/sidebar';
import { AppSidebar } from './nav/AppSidebar';
import { useV2 } from './store';
import { Workspace } from './workspace/Workspace';

/**
 * The shell: a collapsible left navigation sidebar (global / cross-part
 * concerns) beside a floating content card — the shadcn "inset" layout. The
 * card holds the single-part workspace, whose own right rail carries settings
 * scoped to what's on screen.
 */
export default function App() {
  const theme = useV2((s) => s.theme);
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    setViewerTheme(theme); // viewer background + colour-map variants follow the theme
  }, [theme]);

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <Workspace />
      </SidebarInset>
    </SidebarProvider>
  );
}
