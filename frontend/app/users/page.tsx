"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/useApi";
import { ServerTable } from "@/components/data/ServerTable";
import type { User, UserRole } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Input,
  PageHeader,
  Select,
} from "@/components/ui";

const ROLE_TONE: Record<UserRole, "green" | "blue" | "amber" | "slate"> = {
  admin: "green",
  scheduler: "blue",
  faculty: "amber",
  viewer: "slate",
};

const BLANK = { username: "", password: "", role: "viewer" as UserRole };

/**
 * Admin-only (spec PART 12/13: "manage users" is an Admin capability).
 * Not visible in the nav to a non-admin, and the backend independently
 * rejects every call here for anyone else - this page is a convenience on
 * top of that enforcement, not a substitute for it.
 */
export default function UsersPage() {
  const { user: me, isAdmin } = useAuth();
  const { run, error, setError } = useMutation();
  const [form, setForm] = useState(BLANK);

  if (!isAdmin) {
    return (
      <>
        <PageHeader title="Users" />
        <EmptyState>
          {me ? "Only an admin can manage users." : "Sign in as an admin to manage users."}
        </EmptyState>
      </>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const ok = await run(
      () =>
        api.auth.users.create({
          username: form.username.trim(),
          password: form.password,
          role: form.role,
        }),
      ["users"],
    );
    if (ok) setForm(BLANK);
  }

  return (
    <>
      <PageHeader
        title="Users"
        description="Admin can manage all reference data, import data, generate, edit, lock/unlock, publish, export and manage users. Scheduler can do everything except manage reference data, import data or manage users. Faculty and Viewer are read-only."
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="grid gap-6 md:grid-cols-[320px_1fr]">
        <Card title="Add user">
          <form onSubmit={submit} className="space-y-3">
            <Input
              label="Username"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              required
            />
            <Input
              label="Password"
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              hint="At least 8 characters."
              required
              minLength={8}
            />
            <Select
              label="Role"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as UserRole })}
            >
              <option value="viewer">Viewer</option>
              <option value="faculty">Faculty</option>
              <option value="scheduler">Scheduler</option>
              <option value="admin">Admin</option>
            </Select>
            <Button type="submit">Add user</Button>
          </form>
        </Card>

        <div className="min-w-0">
          <ServerTable<User>
            cacheKey="users"
            searchPlaceholder="Search username or email"
            fetcher={(params) => api.auth.users.page(params)}
            empty="No users yet."
            columns={[
              { header: "Username", sortField: "username", cell: (u) => u.username },
              {
                header: "Role",
                sortField: "role",
                cell: (u) => <Badge tone={ROLE_TONE[u.role]}>{u.role}</Badge>,
              },
              {
                header: "Status",
                key: "status",
                cell: (u) =>
                  u.is_active ? (
                    <Badge tone="green">active</Badge>
                  ) : (
                    <Badge tone="red">disabled</Badge>
                  ),
              },
              {
                header: "Last login",
                key: "last-login",
                cell: (u) =>
                  u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "never",
              },
              {
                header: "",
                key: "actions",
                className: "text-right",
                cell: (u) =>
                  u.id === me?.id ? (
                    <span className="text-xs text-ink-faint">you</span>
                  ) : (
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        onClick={() =>
                          run(
                            () =>
                              api.auth.users.update(u.id, { is_active: !u.is_active }),
                            ["users"],
                          )
                        }
                      >
                        {u.is_active ? "Disable" : "Enable"}
                      </Button>
                    </div>
                  ),
              },
            ]}
          />
        </div>
      </div>
    </>
  );
}
