"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";
import { MobileNav } from "./MobileNav";
import { NavRail } from "./NavRail";
import { TopBar } from "./TopBar";
import { AssistantPanel } from "@/components/assistant/AssistantPanel";

/**
 * The chrome around every page.
 *
 * Only the drawer's open/closed state needs a client component, so the client
 * boundary starts here rather than at the root layout - the layout itself
 * stays a Server Component.
 *
 * The main region carries padding equal to the fixed bars so that content
 * begins below the top bar and, on a phone, ends above the bottom navigation
 * instead of hiding its last row underneath it.
 *
 * Signing in is the exception: a rail full of destinations you cannot reach
 * yet, behind a form asking who you are, is chrome pretending to be an
 * application. The login page gets the page to itself.
 */
const BARE = ["/login"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname() ?? "/";

  if (BARE.includes(pathname)) {
    return <main className="min-h-dvh">{children}</main>;
  }

  return (
    <>
      <TopBar onOpenMenu={() => setMenuOpen(true)} />
      <NavRail />
      <MobileNav open={menuOpen} onClose={() => setMenuOpen(false)} />

      {/*
        `overflow-x-clip`, not `hidden`: clip stops a wide table from dragging
        the whole page sideways without making this a scroll container, which
        would break `position: sticky` inside it.
      */}
      <main className="min-h-dvh overflow-x-clip pt-(--spacing-topbar) pb-(--spacing-bottomnav) md:pl-56 md:pb-0">
        <div className="mx-auto max-w-content px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </div>
      </main>
      <AssistantPanel />
    </>
  );
}
