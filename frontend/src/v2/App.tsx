import { useEffect } from 'react';
import { Navbar, NavbarSpacer } from '../catalyst/navbar';
import { SidebarLayout } from '../catalyst/sidebar-layout';
import { setViewerTheme } from '../viewer/controller';
import { AppSidebar } from './nav/AppSidebar';
import { useV2 } from './store';
import { Workspace } from './workspace/Workspace';

/**
 * The shell: Catalyst's SidebarLayout — a fixed left nav rail beside a floating
 * content card (matching the Wefabricate Partner Portal). The card holds the
 * single-part workspace; its own right rail carries settings scoped to what's
 * on screen.
 */
export default function App() {
  const theme = useV2((s) => s.theme);
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    setViewerTheme(theme); // viewer background + colour-map variants follow the theme
  }, [theme]);

  return (
    <SidebarLayout
      sidebar={<AppSidebar />}
      navbar={<Navbar><NavbarSpacer /></Navbar>}
    >
      <Workspace />
    </SidebarLayout>
  );
}
