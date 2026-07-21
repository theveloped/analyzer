import { useEffect, useState } from 'react';
import { Navbar, NavbarSpacer } from '../catalyst/navbar';
import { SidebarLayout } from '../catalyst/sidebar-layout';
import { setViewerTheme } from '../viewer/controller';
import { AppSidebar } from './nav/AppSidebar';
import { ReportView } from './report/ReportView';
import { useV2 } from './store';
import { Workspace } from './workspace/Workspace';

/** `#report=<part>/<rid>` renders the read-only published report. */
function parseReportHash(): { partId: string; rid: string } | null {
  const match = window.location.hash.match(/^#report=([^/]+)\/(.+)$/);
  return match
    ? { partId: decodeURIComponent(match[1]), rid: decodeURIComponent(match[2]) }
    : null;
}

/**
 * The shell: Catalyst's SidebarLayout — a fixed left nav rail beside a floating
 * content card (matching the Wefabricate Partner Portal). The card holds the
 * single-part workspace; its own right rail carries settings scoped to what's
 * on screen.
 */
export default function App() {
  const theme = useV2((s) => s.theme);
  const [reportRoute, setReportRoute] = useState(parseReportHash);
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    setViewerTheme(theme); // viewer background + colour-map variants follow the theme
  }, [theme]);
  useEffect(() => {
    const onHash = () => setReportRoute(parseReportHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  return (
    <SidebarLayout
      sidebar={<AppSidebar />}
      navbar={<Navbar><NavbarSpacer /></Navbar>}
    >
      {reportRoute ? (
        <ReportView
          partId={reportRoute.partId}
          rid={reportRoute.rid}
          onBack={() => { window.location.hash = ''; }}
        />
      ) : (
        <Workspace />
      )}
    </SidebarLayout>
  );
}
