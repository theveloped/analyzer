import { Slot } from '@radix-ui/react-slot';
import { PanelLeft } from 'lucide-react';
import * as React from 'react';
import { cn } from '../../lib/utils';
import { Button } from './button';
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from './tooltip';

/**
 * A compact port of shadcn/ui's sidebar: enough of the API to build the
 * "inset" layout the workspace uses — a collapsible left nav rail with the
 * main content floating as a rounded card beside it. Desktop-focused (no
 * mobile Sheet), which suits an engineering power tool.
 */

interface SidebarContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
}

const SidebarContext = React.createContext<SidebarContextValue | null>(null);

export function useSidebar() {
  const ctx = React.useContext(SidebarContext);
  if (!ctx) throw new Error('useSidebar must be used within a SidebarProvider');
  return ctx;
}

export function SidebarProvider({
  defaultOpen = true, className, children, ...props
}: React.HTMLAttributes<HTMLDivElement> & { defaultOpen?: boolean }) {
  const [open, setOpen] = React.useState(defaultOpen);
  const value = React.useMemo<SidebarContextValue>(
    () => ({ open, setOpen, toggle: () => setOpen((o) => !o) }),
    [open],
  );
  return (
    <SidebarContext.Provider value={value}>
      <TooltipProvider delayDuration={200}>
        <div
          data-state={open ? 'expanded' : 'collapsed'}
          className={cn('flex min-h-svh w-full bg-sidebar text-sidebar-foreground', className)}
          {...props}
        >
          {children}
        </div>
      </TooltipProvider>
    </SidebarContext.Provider>
  );
}

export function Sidebar({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const { open } = useSidebar();
  return (
    <aside
      data-state={open ? 'expanded' : 'collapsed'}
      className={cn(
        'group/sidebar sticky top-0 h-svh shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out',
        open ? 'w-64' : 'w-[3.5rem]',
        className,
      )}
      {...props}
    >
      <div className="flex h-full flex-col gap-1 px-2 py-2">{children}</div>
    </aside>
  );
}

export function SidebarInset({ className, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <main
      className={cn(
        'relative m-2 flex flex-1 flex-col overflow-hidden rounded-xl border bg-background shadow-sm',
        className,
      )}
      {...props}
    />
  );
}

export function SidebarTrigger({ className, ...props }: React.ComponentProps<typeof Button>) {
  const { toggle } = useSidebar();
  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn('size-8', className)}
      onClick={toggle}
      aria-label="Toggle sidebar"
      {...props}
    >
      <PanelLeft />
    </Button>
  );
}

export function SidebarHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex flex-col gap-2', className)} {...props} />;
}

export function SidebarFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('mt-auto flex flex-col gap-2', className)} {...props} />;
}

export function SidebarContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('flex min-h-0 flex-1 flex-col gap-2 overflow-auto', className)}
      {...props}
    />
  );
}

export function SidebarGroup({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex flex-col gap-1', className)} {...props} />;
}

export function SidebarGroupLabel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const { open } = useSidebar();
  return (
    <div
      className={cn(
        'px-2 pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground',
        !open && 'sr-only',
        className,
      )}
      {...props}
    />
  );
}

export function SidebarMenu({ className, ...props }: React.HTMLAttributes<HTMLUListElement>) {
  return <ul className={cn('flex flex-col gap-0.5', className)} {...props} />;
}

export function SidebarMenuItem({ className, ...props }: React.HTMLAttributes<HTMLLIElement>) {
  return <li className={cn('relative', className)} {...props} />;
}

export interface SidebarMenuButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  isActive?: boolean;
  asChild?: boolean;
  tooltip?: string;
}

export const SidebarMenuButton = React.forwardRef<HTMLButtonElement, SidebarMenuButtonProps>(
  ({ className, isActive, asChild, tooltip, children, ...props }, ref) => {
    const { open } = useSidebar();
    const Comp = asChild ? Slot : 'button';
    const button = (
      <Comp
        ref={ref}
        data-active={isActive}
        className={cn(
          'flex h-8 w-full items-center gap-2 overflow-hidden rounded-md px-2 text-sm outline-none transition-colors',
          'hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring',
          '[&>svg]:size-4 [&>svg]:shrink-0',
          isActive && 'bg-sidebar-accent font-medium text-sidebar-accent-foreground',
          !open && 'justify-center px-0',
          className,
        )}
        {...props}
      >
        {children}
      </Comp>
    );
    if (!open && tooltip) {
      return (
        <Tooltip>
          <TooltipTrigger asChild>{button}</TooltipTrigger>
          <TooltipContent side="right">{tooltip}</TooltipContent>
        </Tooltip>
      );
    }
    return button;
  },
);
SidebarMenuButton.displayName = 'SidebarMenuButton';

export function SidebarSeparator({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('mx-2 my-1 h-px bg-sidebar-border', className)} {...props} />;
}

/** Hidden when the sidebar is collapsed — labels, descriptions, etc. */
export function SidebarLabelText({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  const { open } = useSidebar();
  if (!open) return null;
  return <span className={cn('truncate', className)} {...props} />;
}
