import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api } from "../api.js";
import { isAdmin, getUser } from "../auth.js";
import AppShell from "../components/AppShell.jsx";

export default function AdminPage() {
  const [users, setUsers] = useState([]);
  const [usage, setUsage] = useState([]);
  const [projects, setProjects] = useState([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ email: "", name: "", password: "", credits_balance: "100" });

  if (!isAdmin()) return <Navigate to="/" replace />;

  const reload = async () => {
    setError("");
    // Load users first — don't let usage/projects failures hide the user list.
    try {
      const u = await api.adminListUsers();
      setUsers(Array.isArray(u) ? u : []);
    } catch (e) {
      setUsers([]);
      setError(e.message || "Could not load users");
      return;
    }
    try {
      const [log, p] = await Promise.all([api.adminUsage(50), api.listProjects()]);
      setUsage(Array.isArray(log) ? log : []);
      setProjects(Array.isArray(p) ? p : []);
    } catch (e) {
      // Users already loaded — show a soft warning for the rest.
      setError((prev) => prev || e.message);
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const createUser = async (e) => {
    e.preventDefault();
    setError("");
    try {
      await api.adminCreateUser({
        email: form.email.trim(),
        name: form.name.trim(),
        password: form.password,
        credits_balance: parseFloat(form.credits_balance) || 100,
      });
      setForm({ email: "", name: "", password: "", credits_balance: "100" });
      reload();
    } catch (err) {
      setError(err.message);
    }
  };

  const resetPassword = async (u) => {
    const pwd = prompt(
      `New password for ${u.email} (min 6 characters).\nShare it with the user securely.`,
      ""
    );
    if (pwd == null) return;
    if (pwd.trim().length < 6) {
      setError("Password must be at least 6 characters");
      return;
    }
    setError("");
    try {
      await api.adminUpdateUser(u.id, { password: pwd });
      alert(`Password updated for ${u.email}. They can sign in now.`);
    } catch (err) {
      setError(err.message);
    }
  };

  const setCredits = async (id, balance, role) => {
    const hint = role === "admin" ? " (admins are not charged; this is for display only)" : "";
    const val = prompt(`New credit balance${hint}:`, role === "admin" ? "0" : String(balance));
    if (val == null) return;
    await api.adminUpdateUser(id, { credits_balance: parseFloat(val) });
    reload();
  };

  const deleteUser = async (u) => {
    if (!window.confirm(`Delete user ${u.email}? This cannot be undone.`)) return;
    setError("");
    try {
      await api.adminDeleteUser(u.id);
      reload();
    } catch (err) {
      setError(err.message);
    }
  };

  const currentUser = getUser();

  return (
    <AppShell projects={projects} onProjectsChange={reload}>
      <div className="admin-page">
        <h1>Admin</h1>
        <p className="muted">Create users, set passwords, and control credit usage. Admin accounts have unlimited usage (not charged credits).</p>
        {error && <div className="error">{error}</div>}

        <section className="admin-section">
          <h2>Create user</h2>
          <form className="admin-form" onSubmit={createUser}>
            <input placeholder="Email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
            <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input placeholder="Password (min 6)" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required minLength={6} />
            <input placeholder="Starting credits" type="number" step="0.1" value={form.credits_balance} onChange={(e) => setForm({ ...form, credits_balance: e.target.value })} />
            <button type="submit" className="primary">Create user</button>
          </form>
        </section>

        <section className="admin-section">
          <h2>Users ({users.length})</h2>
          {users.length === 0 ? (
            <p className="muted">No users loaded. If you just opened Admin, check the error above — or create a user with the form.</p>
          ) : (
          <table className="admin-table">
            <thead>
              <tr><th>Email</th><th>Name</th><th>Role</th><th>Credits</th><th>Active</th><th></th></tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.email}</td>
                  <td>{u.name}</td>
                  <td>{u.role}</td>
                  <td>{u.role === "admin" ? "Unlimited" : Number(u.credits_balance ?? 0).toFixed(2)}</td>
                  <td>{u.is_active ? "Yes" : "No"}</td>
                  <td>
                    <button type="button" className="secondary small" onClick={() => setCredits(u.id, u.credits_balance, u.role)}>
                      Set credits
                    </button>
                    <button type="button" className="secondary small" onClick={() => resetPassword(u)}>
                      Reset password
                    </button>
                    {currentUser?.id !== u.id && (
                      <button type="button" className="secondary small danger" onClick={() => deleteUser(u)}>
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          )}
        </section>

        <section className="admin-section">
          <h2>Recent usage</h2>
          <table className="admin-table">
            <thead>
              <tr><th>User</th><th>Action</th><th>MB</th><th>Credits</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {usage.map((r) => (
                <tr key={r.id}>
                  <td>{r.user_id}</td>
                  <td>{r.action}</td>
                  <td>{(r.bytes_estimate / 1048576).toFixed(2)}</td>
                  <td>{r.credits_used.toFixed(4)}</td>
                  <td className="admin-detail">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <Link to="/" className="secondary">← Back to projects</Link>
      </div>
    </AppShell>
  );
}
