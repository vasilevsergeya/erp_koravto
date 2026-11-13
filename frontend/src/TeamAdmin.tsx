import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Modal,
  PasswordInput,
  Select,
  TextInput,
} from "@mantine/core";
import { useTranslation } from "react-i18next";
import { api, type User } from "./api";
import "./v1-i18n";

type Member = {
  id: string;
  email: string;
  role: string;
  active: boolean;
  must_change_password: boolean;
  access_version: number;
};
type Link = { url: string; expires_at: string; email_status: string };
const fail = (e: unknown) => (e instanceof Error ? e.message : String(e));

const accessToken =
  new URLSearchParams(location.hash.slice(1)).get("access") || "";
export function AccessLink() {
  const { t } = useTranslation("v1");
  const token = accessToken;
  useEffect(() => {
    history.replaceState(null, "", location.pathname);
  }, []);
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <div className="loading full">
      <section
        className="surface settings-card"
        style={{ maxWidth: 480, width: "100%" }}
      >
        <h2>{t("setPassword")}</h2>
        <p>{t(done ? "passwordSet" : "linkHint")}</p>
        {error && <Alert color="red">{error}</Alert>}
        {done ? (
          <Button component="a" href="/">
            {t("login")}
          </Button>
        ) : (
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              try {
                await api("/api/auth/access", "POST", { token, password });
                setDone(true);
                setPassword("");
              } catch (e) {
                setError(fail(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <PasswordInput
              required
              minLength={7}
              maxLength={256}
              autoComplete="new-password"
              label={t("password")}
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
            />
            <Button mt="md" type="submit" loading={busy}>
              {t("save")}
            </Button>
          </form>
        )}
      </section>
    </div>
  );
}

export function TeamAdmin({ user }: { user: User }) {
  const { t } = useTranslation("v1");
  const query = useQueryClient();
  const data = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => api<Member[]>("/api/admin/users"),
  });
  const [email, setEmail] = useState(""),
    [role, setRole] = useState("manager"),
    [mail, setMail] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [link, setLink] = useState<Link | null>(null);
  const [edit, setEdit] = useState<Member | null>(null);
  const roles = ["admin", "manager", "staff"].map((value) => ({
    value,
    label: t(value),
  }));
  async function act(path: string, method: string, body: unknown) {
    setBusy(true);
    setError("");
    try {
      const result = await api<Link>(path, method, body);
      if (result.url) setLink(result);
      setEdit(null);
      await query.invalidateQueries({ queryKey: ["admin-users"] });
      await query.invalidateQueries({ queryKey: ["team"] });
      return true;
    } catch (e) {
      setError(fail(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="settings-page">
      <section className="surface settings-card">
        <h3>{t("invite")}</h3>
        <p>{t("inviteHint")}</p>
        {(error || data.error) && (
          <Alert color="red">{error || fail(data.error)}</Alert>
        )}
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (
              await act("/api/admin/users", "POST", {
                email,
                role,
                send_email: mail,
              })
            )
              setEmail("");
          }}
        >
          <TextInput
            type="email"
            required
            label="Email"
            value={email}
            onChange={(e) => setEmail(e.currentTarget.value)}
            maxLength={254}
          />
          <Select
            mt="sm"
            label={t("role")}
            data={roles}
            value={role}
            onChange={(v) => setRole(v || "manager")}
          />
          <Checkbox
            mt="sm"
            label={t("emailLink")}
            checked={mail}
            onChange={(e) => setMail(e.currentTarget.checked)}
          />
          <Button mt="md" type="submit" loading={busy}>
            {t("invite")}
          </Button>
        </form>
      </section>
      <section className="surface settings-card">
        <h3>{t("employees")}</h3>
        {data.data?.map((m) => (
          <div className="setting-row" key={m.id}>
            <div style={{ minWidth: 0, overflowWrap: "anywhere" }}>
              <b>{m.email}</b>
              <p>
                {t(m.role)} · {t(m.active ? "active" : "inactive")}{" "}
                {m.must_change_password && (
                  <Badge size="xs">{t("awaitPassword")}</Badge>
                )}
              </p>
            </div>
            <Button
              variant="light"
              disabled={m.id === user.id || busy}
              onClick={() => setEdit({ ...m })}
            >
              {t("access")}
            </Button>
          </div>
        ))}
      </section>
      <Modal
        closeButtonProps={{ "aria-label": t("close") }}
        opened={!!edit}
        onClose={() => setEdit(null)}
        title={edit?.email}
      >
        {edit && (
          <>
            <Select
              label={t("role")}
              data={roles}
              value={edit.role}
              onChange={(v) => setEdit({ ...edit, role: v || edit.role })}
            />
            <Checkbox
              mt="md"
              label={t("active")}
              checked={edit.active}
              onChange={(e) =>
                setEdit({ ...edit, active: e.currentTarget.checked })
              }
            />
            <p>{t("accessHint")}</p>
            <Button
              loading={busy}
              onClick={() =>
                void act("/api/admin/users/" + edit.id, "PATCH", {
                  role: edit.role,
                  active: edit.active,
                  version: edit.access_version,
                })
              }
            >
              {t("save")}
            </Button>
            <Button
              mt="md"
              color="orange"
              variant="light"
              fullWidth
              disabled={!edit.active || busy}
              onClick={() => {
                if (window.confirm(t("resetConfirm")))
                  void act(
                    "/api/admin/users/" + edit.id + "/reset-password",
                    "POST",
                    { version: edit.access_version, send_email: mail },
                  );
              }}
            >
              {t("resetPassword")}
            </Button>
            {error && (
              <Alert mt="md" color="red">
                {error}
              </Alert>
            )}
          </>
        )}
      </Modal>
      <Modal
        closeButtonProps={{ "aria-label": t("close") }}
        opened={!!link}
        onClose={() => setLink(null)}
        title={t("accessLink")}
      >
        {link && (
          <>
            <Alert color={link.email_status === "sent" ? "teal" : "orange"}>
              {t("mail_" + link.email_status, {
                defaultValue: t("mail_failed"),
              })}
            </Alert>
            <p>{t("linkHint")}</p>
            <TextInput
              label={t("accessLink")}
              readOnly
              value={link.url}
              onFocus={(e) => e.currentTarget.select()}
            />
            <p>
              {t("expires")} {new Date(link.expires_at).toLocaleString()}
            </p>
          </>
        )}
      </Modal>
    </div>
  );
}
