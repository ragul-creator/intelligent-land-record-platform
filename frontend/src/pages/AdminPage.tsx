import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  addProjectMember,
  deleteProject,
  loadProject,
  loadProjectMembers,
  removeProjectMember,
  searchUsers,
  updateProject,
  updateProjectMember,
  type ApplicationRole,
} from "../api/admin";
import { loadCurrentUser } from "../api/gis";

const roles: ApplicationRole[] = ["ADMIN", "OFFICER", "REVIEWER", "SURVEYOR", "VIEWER"];

export function AdminPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [memberQuery, setMemberQuery] = useState("");
  const [selectedUserId, setSelectedUserId] = useState("");
  const [selectedRole, setSelectedRole] = useState<ApplicationRole>("VIEWER");
  const [message, setMessage] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");

  const currentUser = useQuery({ queryKey: ["current-user"], queryFn: loadCurrentUser, retry: false });
  const permissions = currentUser.data?.permissions ?? [];
  const canUpdate = permissions.includes("project:update");
  const canManageMembers = permissions.includes("project:member_manage");
  const canSearchUsers = permissions.includes("user:manage");

  const project = useQuery({
    queryKey: ["project-admin", projectId],
    queryFn: () => loadProject(projectId!),
    enabled: Boolean(projectId && currentUser.data && (canUpdate || canManageMembers)),
    retry: false,
  });
  const members = useQuery({
    queryKey: ["project-members", projectId],
    queryFn: () => loadProjectMembers(projectId!),
    enabled: Boolean(projectId && currentUser.data && (canUpdate || canManageMembers)),
    retry: false,
  });
  const users = useQuery({
    queryKey: ["user-directory", memberQuery],
    queryFn: () => searchUsers(memberQuery.trim()),
    enabled: Boolean(canSearchUsers && memberQuery.trim().length >= 2),
    retry: false,
  });
  const isProjectOwner = Boolean(currentUser.data?.id && project.data?.owner_id === currentUser.data.id);

  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftState, setDraftState] = useState<"ACTIVE" | "ARCHIVED">("ACTIVE");

  useEffect(() => {
    if (!project.data) return;
    setDraftName(project.data.name);
    setDraftDescription(project.data.description ?? "");
    setDraftState(project.data.state);
  }, [project.data]);

  const formValues = {
    name: draftName,
    description: draftDescription,
    state: draftState,
  };

  const saveProject = useMutation({
    mutationFn: () => updateProject(projectId!, {
      name: formValues.name.trim(),
      description: formValues.description.trim() || null,
      state: formValues.state,
    }),
    onSuccess: async () => {
      setMessage("Project settings saved.");
      await client.invalidateQueries({ queryKey: ["project-admin", projectId] });
      await client.invalidateQueries({ queryKey: ["project-dashboard", projectId] });
      await client.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const addMember = useMutation({
    mutationFn: () => addProjectMember(projectId!, selectedUserId, selectedRole),
    onSuccess: async () => {
      setMessage("Project member added.");
      setSelectedUserId("");
      await client.invalidateQueries({ queryKey: ["project-members", projectId] });
    },
  });
  const changeMember = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: ApplicationRole }) => updateProjectMember(projectId!, userId, role),
    onSuccess: async () => {
      setMessage("Member role updated.");
      await client.invalidateQueries({ queryKey: ["project-members", projectId] });
    },
  });
  const deleteMember = useMutation({
    mutationFn: (userId: string) => removeProjectMember(projectId!, userId),
    onSuccess: async () => {
      setMessage("Project member removed.");
      await client.invalidateQueries({ queryKey: ["project-members", projectId] });
    },
  });
  const deleteProjectMutation = useMutation({
    mutationFn: () => deleteProject(projectId!, deleteConfirmation),
    onSuccess: async () => {
      client.removeQueries({ queryKey: ["project-admin", projectId] });
      client.removeQueries({ queryKey: ["project-dashboard", projectId] });
      await client.invalidateQueries({ queryKey: ["projects"] });
      navigate("/", { replace: true });
    },
  });

  if (!projectId) return <main className="tool-state"><h1>Project administration unavailable</h1></main>;
  if (currentUser.isLoading) return <main className="tool-state"><h1>Loading access…</h1></main>;
  if (!canUpdate && !canManageMembers) return <main className="tool-state"><h1>Project administration unavailable</h1><p>Your account does not have project administration permissions.</p><Link to={`/projects/${projectId}`}>Return to dashboard</Link></main>;

  return <main className="tool-shell">
    <header className="tool-header"><div><p className="eyebrow">H.2B.4 administration</p><h1>Project management</h1><p>Manage project metadata and authorized membership. Backend RBAC remains authoritative.</p></div><nav><Link to={`/projects/${projectId}`}>Dashboard</Link><Link to="/">Platform home</Link></nav></header>
    <nav className="admin-segments" aria-label="Project administration sections">
      {canUpdate && <a href="#project-settings">Project settings</a>}
      <a href="#project-members">Members</a>
      {isProjectOwner && <a href="#danger-zone">Danger zone</a>}
    </nav>

    {message && <p className="tool-success" role="status">{message}</p>}
    {(saveProject.isError || addMember.isError || changeMember.isError || deleteMember.isError || deleteProjectMutation.isError) && <p className="tool-error" role="alert">{deleteProjectMutation.isError ? "Project deletion failed. The project and its data were not removed." : "The requested administration change could not be saved."}</p>}

    {canUpdate && <section className="tool-card" id="project-settings">
      <h2>Project settings</h2>
      {project.isLoading && <p>Loading project…</p>}
      {project.data && <form className="tool-form" onSubmit={(event) => { event.preventDefault(); saveProject.mutate(); }}>
        <label>Project name<input value={formValues.name} onChange={(event) => setDraftName(event.target.value)} /></label>
        <label>Description<textarea value={formValues.description} onChange={(event) => setDraftDescription(event.target.value)} /></label>
        <label>State<select value={formValues.state} onChange={(event) => setDraftState(event.target.value as "ACTIVE" | "ARCHIVED")}><option value="ACTIVE">ACTIVE</option><option value="ARCHIVED">ARCHIVED</option></select></label>
        <button type="submit" disabled={!formValues.name.trim() || saveProject.isPending}>Save project</button>
      </form>}
    </section>}

    <section className="tool-card" id="project-members">
      <div className="tool-card-heading"><div><h2>Project members</h2><p>{members.data?.page.total ?? 0} authorized members</p></div></div>
      {canManageMembers && canSearchUsers && <div className="member-add-panel">
        <label>Find user<input aria-label="Find user" value={memberQuery} onChange={(event) => setMemberQuery(event.target.value)} placeholder="Login ID, name, or email" /></label>
        {users.data && <label>User<select aria-label="User to add" value={selectedUserId} onChange={(event) => {
          const id = event.target.value;
          setSelectedUserId(id);
          const candidate = users.data?.items.find((item) => item.id === id);
          const firstRole = candidate?.roles.find((role): role is ApplicationRole => roles.includes(role as ApplicationRole));
          if (firstRole) setSelectedRole(firstRole);
        }}><option value="">Select a user</option>{users.data.items.map((item) => <option key={item.id} value={item.id}>{item.login_id} · {item.full_name}</option>)}</select></label>}
        <label>Project role<select aria-label="Project role" value={selectedRole} onChange={(event) => setSelectedRole(event.target.value as ApplicationRole)}>{roles.map((role) => <option key={role}>{role}</option>)}</select></label>
        <button type="button" disabled={!selectedUserId || addMember.isPending} onClick={() => addMember.mutate()}>Add member</button>
        <small>The target user must already hold the selected application role; project membership never escalates global RBAC.</small>
      </div>}
      <div className="member-table" role="table" aria-label="Project members">
        {members.data?.items.map((member) => {
          const owner = project.data?.owner_id === member.user_id;
          return <article key={member.user_id} className="member-row">
            <div><strong>{member.full_name}</strong><span>{member.login_id}</span><small>{member.is_active ? "Active" : "Inactive"}{owner ? " · Project owner" : ""}</small></div>
            <div>
              <select aria-label={`Role for ${member.login_id}`} value={member.role} disabled={!canManageMembers || owner || changeMember.isPending} onChange={(event) => changeMember.mutate({ userId: member.user_id, role: event.target.value as ApplicationRole })}>{roles.map((role) => <option key={role}>{role}</option>)}</select>
              {canManageMembers && !owner && <button type="button" disabled={deleteMember.isPending} onClick={() => deleteMember.mutate(member.user_id)}>Remove</button>}
            </div>
          </article>;
        })}
      </div>
    </section>

    {isProjectOwner && project.data && <section className="admin-danger-zone" id="danger-zone" aria-labelledby="danger-zone-title">
      <div className="admin-danger-copy">
        <p className="eyebrow">Danger zone</p>
        <h2 id="danger-zone-title">Delete project</h2>
        <p>Permanently remove this project and its documents, GIS layers, processing jobs, reviews, and project-scoped evidence. Platform audit events are retained with the project reference cleared.</p>
      </div>
      <div className="admin-danger-confirm">
        <label>
          Type <strong>{project.data.name}</strong> to confirm
          <input
            aria-label="Confirm project name"
            autoComplete="off"
            value={deleteConfirmation}
            onChange={(event) => setDeleteConfirmation(event.target.value)}
            placeholder={project.data.name}
          />
        </label>
        <button
          type="button"
          className="danger-action"
          disabled={deleteConfirmation !== project.data.name || deleteProjectMutation.isPending}
          onClick={() => {
            if (window.confirm(`Permanently delete "${project.data.name}" and all project data? This cannot be undone.`)) {
              deleteProjectMutation.mutate();
            }
          }}
        >
          {deleteProjectMutation.isPending ? "Deleting project…" : "Delete project permanently"}
        </button>
      </div>
    </section>}
  </main>;
}
