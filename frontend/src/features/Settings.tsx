import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { z } from "zod";
import {
  Cable,
  Check,
  Copy,
  Cpu,
  Download,
  HardDrive,
  Lightbulb,
  MessageSquareCode,
  PenLine,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  ScrollText,
  SearchCheck,
  Trash2,
  Type,
  Upload,
  X,
} from "lucide-react";
import {
  Alert,
  Badge,
  Button,
  Dialog,
  EmptyState,
  Input,
  Select,
  Switch,
  Textarea,
} from "../components/ui";
import { ApiError, errorText, remove, request, upload } from "../lib/api";
import {
  DEFAULT_EDITOR_PREFS,
  loadEditorPrefs,
  saveEditorPrefs,
  type EditorPrefs,
} from "../lib/editorPrefs";
import {
  backupRecordSchema,
  bookSchema,
  catalogLookupSchema,
  catalogStatusSchema,
  discoveredSchema,
  modelSchema,
  presetSchema,
  promptSchema,
  protocolSchema,
  providerSchema,
  settingsSchema,
  systemStatusSchema,
  type BackupRecord,
  type Book,
  type CatalogStatus,
  type CatalogLookup,
  type Model,
  type Provider,
  type SystemStatus,
} from "../lib/schema";
import "./settings.css";

type Preset = z.infer<typeof presetSchema>;
type Prompt = z.infer<typeof promptSchema>;
type GlobalSettings = z.infer<typeof settingsSchema>;
type Discovered = z.infer<typeof discoveredSchema>;
type SectionId = "providers" | "models" | "rules" | "prompts" | "editor" | "data";
type SecretMode = "unchanged" | "editing" | "cleared";

const protocols = protocolSchema.options.map((value) => ({ value, label: value }));

const modeMeta = [
  {
    value: "discuss",
    label: "讨论模式",
    icon: Lightbulb,
    toolsHint: "适合头脑风暴与梳理设定：可阅读正文与设定，可提交设定修改建议，不会改写章节正文。",
  },
  {
    value: "write",
    label: "写作模式",
    icon: PenLine,
    toolsHint: "适合正文起草与润色：可阅读全书资料，修改正文或设定均进入待审区由你确认。",
  },
  {
    value: "review",
    label: "审阅模式",
    icon: SearchCheck,
    toolsHint: "适合校对与伏笔核查：严格只读全部章节与设定，不会提出或写入任何修改。",
  },
] as const;

const sections: readonly { value: SectionId; label: string; icon: typeof Cable }[] = [
  { value: "providers", label: "提供商", icon: Cable },
  { value: "models", label: "模型", icon: Cpu },
  { value: "rules", label: "写作规则", icon: ScrollText },
  { value: "prompts", label: "写作模式", icon: MessageSquareCode },
  { value: "editor", label: "编辑与阅读", icon: Type },
  { value: "data", label: "数据与服务", icon: HardDrive },
];

export function Settings({
  open,
  onOpenChange,
  onSaved,
  activeBook,
  books,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  activeBook?: Book | null;
  books?: Book[];
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="设置"
      hideHeader
      className="settings-dialog"
      wide
    >
      {open && (
        <SettingsContent
          onSaved={onSaved}
          activeBook={activeBook ?? null}
          books={books ?? (activeBook ? [activeBook] : [])}
        />
      )}
    </Dialog>
  );
}

function SettingsContent({
  onSaved,
  activeBook,
  books,
}: {
  onSaved: () => void;
  activeBook: Book | null;
  books: Book[];
}) {
  const [tab, setTab] = useState<SectionId>("providers");
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [settings, setSettings] = useState<GlobalSettings | null>(null);
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [selectedProviderId, setSelectedProviderId] = useState("new");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [deletingProvider, setDeletingProvider] = useState<Provider | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let disposed = false;
    Promise.all([
      request("/providers", providerSchema.array()),
      request("/models", modelSchema.array()),
      request("/provider-presets", presetSchema.array()),
      request("/settings", settingsSchema),
      request("/settings/prompts", promptSchema.array()),
    ])
      .then(([nextProviders, nextModels, nextPresets, nextSettings, nextPrompts]) => {
        if (disposed) return;
        setProviders(nextProviders);
        setModels(nextModels);
        setPresets(nextPresets);
        setSettings(nextSettings);
        setPrompts(nextPrompts);
        setSelectedProviderId(nextProviders[0]?.id ?? "new");
        setLoading(false);
      })
      .catch((failure: unknown) => {
        if (!disposed) {
          setError(errorText(failure));
          setLoading(false);
        }
      });
    return () => {
      disposed = true;
    };
  }, []);

  function providerSaved(provider: Provider) {
    setProviders((items) => {
      const exists = items.some((item) => item.id === provider.id);
      return exists
        ? items.map((item) => (item.id === provider.id ? provider : item))
        : [...items, provider];
    });
    setSelectedProviderId(provider.id);
    onSaved();
  }

  function modelSaved(model: Model) {
    setModels((items) => {
      const exists = items.some((item) => item.id === model.id);
      return exists
        ? items.map((item) => (item.id === model.id ? model : item))
        : [...items, model];
    });
    onSaved();
  }

  async function reloadModels() {
    try {
      const next = await request("/models", modelSchema.array());
      setModels(next);
      onSaved();
    } catch {
      /* Ignore transient refresh failures; user actions surface their own errors. */
    }
  }

  async function deleteProvider() {
    if (!deletingProvider) return;
    setBusy(true);
    setError("");
    try {
      await remove(`/providers/${deletingProvider.id}`);
      const remaining = providers.filter((item) => item.id !== deletingProvider.id);
      setProviders(remaining);
      setModels((items) => items.filter((item) => item.provider_id !== deletingProvider.id));
      setSelectedProviderId(remaining[0]?.id ?? "new");
      setDeletingProvider(null);
      onSaved();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  const activeSection = sections.find((s) => s.value === tab) ?? {
    value: "providers",
    label: "提供商",
    icon: Cable,
  };
  const selectedProvider = providers.find((item) => item.id === selectedProviderId);
  const enabledModels = models.filter(
    (model) =>
      model.enabled && providers.some((item) => item.id === model.provider_id && item.enabled),
  );

  return (
    <div className="settings-layout">
      <nav className="settings-navigation" aria-label="设置分类">
        <div className="settings-sidebar-title">设置</div>
        <div className="settings-sidebar-list">
          {sections.map((section) => {
            const Icon = section.icon;
            return (
              <button
                key={section.value}
                type="button"
                className={`settings-nav-item ${tab === section.value ? "is-active" : ""}`}
                aria-current={tab === section.value ? "page" : undefined}
                onClick={() => setTab(section.value)}
              >
                <Icon size={16} />
                <span>{section.label}</span>
              </button>
            );
          })}
        </div>
      </nav>

      <div className="settings-content">
        <header className="settings-pane-header">
          <h2>{activeSection.label}</h2>
          <DialogPrimitive.Close asChild>
            <Button variant="ghost" className="icon-control" aria-label="关闭设置">
              <X size={17} />
            </Button>
          </DialogPrimitive.Close>
        </header>

        <div className="settings-scroll">
          <Alert>{error}</Alert>
          {loading ? (
            <p className="muted" role="status">
              正在读取设置…
            </p>
          ) : (
            <>
              {tab === "providers" && (
                <div className="providers-split">
                  <div className="provider-list-pane" aria-label="提供商列表">
                    <div className="provider-list-header">
                      <span>已配置</span>
                      <Button
                        variant="ghost"
                        className="icon-control"
                        title="添加提供商"
                        aria-label="添加提供商"
                        onClick={() => setSelectedProviderId("new")}
                      >
                        <Plus size={15} />
                      </Button>
                    </div>
                    {providers.map((item) => (
                      <button
                        type="button"
                        key={item.id}
                        className={`provider-list-item ${selectedProviderId === item.id ? "is-selected" : ""}`}
                        onClick={() => setSelectedProviderId(item.id)}
                      >
                        <span
                          className={`provider-dot ${item.enabled ? "" : "is-disabled"}`}
                          aria-hidden="true"
                        />
                        <span>{item.name}</span>
                      </button>
                    ))}
                    {selectedProviderId === "new" && (
                      <button type="button" className="provider-list-item is-selected">
                        <Plus size={14} />
                        <span>新建提供商</span>
                      </button>
                    )}
                  </div>

                  <div className="provider-detail-pane">
                    <ProviderEditor
                      key={selectedProviderId}
                      provider={selectedProvider}
                      presets={presets}
                      onSaved={providerSaved}
                      onDelete={
                        selectedProvider ? () => setDeletingProvider(selectedProvider) : undefined
                      }
                    />
                  </div>
                </div>
              )}

              {tab === "models" && (
                <ModelsSection
                  providers={providers}
                  models={models}
                  enabledModels={enabledModels}
                  settings={settings}
                  onSettingsSaved={(value) => {
                    setSettings(value);
                    onSaved();
                  }}
                  onModelSaved={modelSaved}
                  onReloadModels={reloadModels}
                  onModelDeleted={(id) => {
                    setModels((items) => items.filter((item) => item.id !== id));
                    onSaved();
                  }}
                />
              )}

              {tab === "rules" && settings && (
                <RulesSection
                  settings={settings}
                  activeBook={activeBook}
                  books={books}
                  onSettingsSaved={(value) => {
                    setSettings(value);
                    onSaved();
                  }}
                  onBookSaved={onSaved}
                />
              )}

              {tab === "prompts" && <PromptEditor initial={prompts} onSaved={onSaved} />}

              {tab === "editor" && <EditorPrefsSection />}

              {tab === "data" && (
                <DataServicePanel activeBook={activeBook} books={books} onBookImported={onSaved} />
              )}
            </>
          )}
        </div>
      </div>

      <Dialog
        open={deletingProvider !== null}
        onOpenChange={(value) => {
          if (!value && !busy) setDeletingProvider(null);
        }}
        title="删除提供商"
        description={`确定删除「${deletingProvider?.name ?? ""}」及其下的模型配置吗？不会影响作品与对话记录。`}
      >
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setDeletingProvider(null)}>
            取消
          </Button>
          <Button variant="danger" disabled={busy} onClick={() => void deleteProvider()}>
            确认删除
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

function ProviderEditor({
  provider,
  presets,
  onSaved,
  onDelete,
}: {
  provider?: Provider;
  presets: Preset[];
  onSaved: (provider: Provider) => void;
  onDelete?: () => void;
}) {
  const [name, setName] = useState(provider?.name ?? "");
  const [preset, setPreset] = useState(provider?.preset || "custom");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [discoveryUrl, setDiscoveryUrl] = useState(provider?.discovery_url ?? "");
  const [protocol, setProtocol] = useState(provider?.default_protocol ?? "openai-chat");
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);

  const [keyMode, setKeyMode] = useState<SecretMode>(
    provider?.api_key_configured ? "unchanged" : "editing",
  );
  const [apiKey, setApiKey] = useState("");
  const [headersMode, setHeadersMode] = useState<SecretMode>(
    provider?.headers_configured ? "unchanged" : "editing",
  );
  const [headers, setHeaders] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const [error, setError] = useState("");

  const presetLabel =
    presets.find((item) => item.id === (provider?.preset || preset))?.name ?? "自定义";

  const isDirty = !provider
    ? Boolean(name.trim() || baseUrl.trim() || apiKey.trim() || headers.trim())
    : name.trim() !== provider.name ||
      baseUrl.trim() !== provider.base_url ||
      discoveryUrl.trim() !== provider.discovery_url ||
      protocol !== provider.default_protocol ||
      enabled !== provider.enabled ||
      keyMode === "cleared" ||
      (keyMode === "editing" && apiKey.length > 0) ||
      headersMode === "cleared" ||
      (headersMode === "editing" && headers.trim().length > 0);

  function choosePreset(value: string) {
    const chosen = presets.find((item) => item.id === value);
    setPreset(value);
    if (chosen) {
      setBaseUrl(chosen.base_url);
      setDiscoveryUrl(chosen.discovery_url);
      setProtocol(chosen.default_protocol);
      if (!name.trim()) setName(chosen.name);
    }
    setSavedNotice(false);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setSavedNotice(false);
    try {
      let parsedHeaders: Record<string, string> | undefined;
      if (headersMode === "cleared") {
        parsedHeaders = {};
      } else if (headersMode === "editing" && headers.trim()) {
        try {
          const raw: unknown = JSON.parse(headers);
          parsedHeaders = z.record(z.string(), z.string()).parse(raw);
        } catch {
          throw new Error("自定义请求头必须是 JSON 对象，且每个值都是文本。");
        }
      }

      const saved = await request(
        provider ? `/providers/${provider.id}` : "/providers",
        providerSchema,
        {
          method: provider ? "PATCH" : "POST",
          body: {
            name: name.trim(),
            preset,
            base_url: baseUrl.trim(),
            discovery_url: discoveryUrl.trim(),
            default_protocol: protocol,
            enabled,
            ...(keyMode === "cleared"
              ? { api_key: "" }
              : keyMode === "editing" && apiKey
                ? { api_key: apiKey }
                : {}),
            ...(parsedHeaders === undefined ? {} : { headers: parsedHeaders }),
          },
        },
      );
      setApiKey("");
      setHeaders("");
      setKeyMode(saved.api_key_configured ? "unchanged" : "editing");
      setHeadersMode(saved.headers_configured ? "unchanged" : "editing");
      setSavedNotice(true);
      onSaved(saved);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="settings-section-form" onSubmit={(event) => void save(event)}>
      <div className="settings-section-bar">
        <div className="row">
          <h3>{provider ? provider.name : "新建提供商"}</h3>
          {provider && <Badge>{presetLabel}</Badge>}
        </div>
        <div className="row">
          {savedNotice && !isDirty && <Badge tone="success">已保存</Badge>}
          {provider && (
            <div className="switch-field">
              <span className="muted">{enabled ? "已启用" : "已停用"}</span>
              <Switch
                checked={enabled}
                disabled={saving}
                label={enabled ? "停用提供商" : "启用提供商"}
                onCheckedChange={(checked) => {
                  setEnabled(checked);
                  setSavedNotice(false);
                }}
              />
            </div>
          )}
          {onDelete && (
            <Button
              variant="ghost"
              className="icon-control"
              title="删除提供商"
              aria-label="删除提供商"
              disabled={saving}
              onClick={onDelete}
            >
              <Trash2 size={15} />
            </Button>
          )}
          <Button type="submit" variant="primary" disabled={saving || !isDirty}>
            {saving ? "保存中…" : "保存"}
          </Button>
        </div>
      </div>

      {!provider && (
        <Select
          label="服务预设（自动填充连接参数）"
          value={preset}
          onChange={choosePreset}
          options={presets.map((item) => ({ value: item.id, label: item.name }))}
        />
      )}

      <Input
        label="名称"
        value={name}
        onChange={(event) => {
          setName(event.target.value);
          setSavedNotice(false);
        }}
        required
        placeholder="例如：DeepSeek"
      />

      <div className="secret-field">
        <span className="secret-field-label">API Key</span>
        {keyMode === "unchanged" ? (
          <div className="secret-saved-bar">
            <span className="secret-status">
              <span className="secret-dot is-ok" aria-hidden="true" />
              <span>已配置密钥 · ••••••••</span>
            </span>
            <div className="row">
              <Button
                onClick={() => {
                  setKeyMode("editing");
                  setApiKey("");
                  setSavedNotice(false);
                }}
              >
                更换密钥
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setKeyMode("cleared");
                  setApiKey("");
                  setSavedNotice(false);
                }}
              >
                移除
              </Button>
            </div>
          </div>
        ) : keyMode === "cleared" ? (
          <div className="secret-saved-bar is-clearing">
            <span className="secret-status">将在点击保存后移除已保存的密钥</span>
            <Button
              variant="ghost"
              onClick={() => {
                setKeyMode("unchanged");
                setSavedNotice(false);
              }}
            >
              撤销
            </Button>
          </div>
        ) : (
          <div className="secret-input-row">
            <Input
              type="password"
              autoComplete="new-password"
              aria-label="API Key"
              placeholder={provider?.api_key_configured ? "输入新的 API Key…" : "粘贴 API Key…"}
              value={apiKey}
              onChange={(event) => {
                setApiKey(event.target.value);
                setSavedNotice(false);
              }}
            />
            {apiKey && (
              <Button variant="ghost" onClick={() => setApiKey("")}>
                清空输入
              </Button>
            )}
            {provider?.api_key_configured && (
              <Button
                variant="ghost"
                onClick={() => {
                  setKeyMode("unchanged");
                  setApiKey("");
                }}
              >
                取消更换
              </Button>
            )}
          </div>
        )}
      </div>

      {!provider && (
        <div className="settings-grid-2">
          <Input
            label="API 地址"
            type="url"
            value={baseUrl}
            onChange={(event) => {
              setBaseUrl(event.target.value);
              setSavedNotice(false);
            }}
            required
            placeholder="https://…/v1"
          />
          <Select
            label="默认协议"
            value={protocol}
            onChange={(value) => {
              setProtocol(protocolSchema.parse(value));
              setSavedNotice(false);
            }}
            options={protocols}
          />
        </div>
      )}

      <details className="settings-advanced" open={!provider}>
        <summary>高级连接选项</summary>
        <div className="stack">
          {provider && (
            <div className="settings-grid-2">
              <Input
                label="API 地址"
                type="url"
                value={baseUrl}
                onChange={(event) => {
                  setBaseUrl(event.target.value);
                  setSavedNotice(false);
                }}
                required
                placeholder="https://…/v1"
              />
              <Select
                label="默认协议"
                value={protocol}
                onChange={(value) => {
                  setProtocol(protocolSchema.parse(value));
                  setSavedNotice(false);
                }}
                options={protocols}
              />
            </div>
          )}
          <Input
            label="模型发现地址（可选）"
            type="url"
            value={discoveryUrl}
            onChange={(event) => {
              setDiscoveryUrl(event.target.value);
              setSavedNotice(false);
            }}
            placeholder="留空则使用 API 地址 /models"
          />
          <div className="secret-field">
            <span className="secret-field-label">自定义请求头</span>
            {headersMode === "unchanged" ? (
              <div className="secret-saved-bar">
                <span className="secret-status">
                  <span className="secret-dot is-ok" aria-hidden="true" />
                  <span>已配置自定义请求头</span>
                </span>
                <div className="row">
                  <Button
                    onClick={() => {
                      setHeadersMode("editing");
                      setHeaders("");
                      setSavedNotice(false);
                    }}
                  >
                    更换
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setHeadersMode("cleared");
                      setHeaders("");
                      setSavedNotice(false);
                    }}
                  >
                    移除
                  </Button>
                </div>
              </div>
            ) : headersMode === "cleared" ? (
              <div className="secret-saved-bar is-clearing">
                <span className="secret-status">将在点击保存后清除自定义请求头</span>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setHeadersMode("unchanged");
                    setSavedNotice(false);
                  }}
                >
                  撤销
                </Button>
              </div>
            ) : (
              <div className="stack">
                <Textarea
                  aria-label="自定义请求头"
                  value={headers}
                  onChange={(event) => {
                    setHeaders(event.target.value);
                    setSavedNotice(false);
                  }}
                  rows={3}
                  placeholder={'{"X-Custom-Header": "value"}'}
                  spellCheck={false}
                />
                {provider?.headers_configured && (
                  <div className="row end">
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setHeadersMode("unchanged");
                        setHeaders("");
                      }}
                    >
                      取消更换
                    </Button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </details>

      <Alert>{error}</Alert>
    </form>
  );
}

function formatCatalogDate(iso: string | null): string {
  if (!iso) return "";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "";
  return new Date(ts).toLocaleDateString("zh-CN");
}

function formatCompactTokens(value: number): string {
  if (value >= 1_000_000) {
    const m = value / 1_000_000;
    return `${Number.isInteger(m) ? m : m.toFixed(1)}M`;
  }
  if (value >= 1_000) {
    const k = value / 1_000;
    return `${Number.isInteger(k) ? k : k.toFixed(0)}K`;
  }
  return String(value);
}

function formatUpstreamSource(source: "server" | "catalog" | null): string {
  if (source === "server") return "服务端";
  if (source === "catalog") return "目录";
  return "上游";
}

function formatParamField(field: "protocol" | "context_window" | "max_output_tokens"): string {
  if (field === "protocol") return "协议";
  if (field === "context_window") return "上下文窗口";
  return "最大输出";
}

function formatParamValue(
  field: "protocol" | "context_window" | "max_output_tokens",
  value: string | number,
): string {
  return field === "protocol" ? String(value) : Number(value).toLocaleString();
}

function ModelsSection({
  providers,
  models,
  enabledModels,
  settings,
  onSettingsSaved,
  onModelSaved,
  onReloadModels,
  onModelDeleted,
}: {
  providers: Provider[];
  models: Model[];
  enabledModels: Model[];
  settings: GlobalSettings | null;
  onSettingsSaved: (settings: GlobalSettings) => void;
  onModelSaved: (model: Model) => void;
  onReloadModels: () => Promise<void>;
  onModelDeleted: (id: string) => void;
}) {
  const [catalogStatus, setCatalogStatus] = useState<CatalogStatus | null>(null);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState("");

  useEffect(() => {
    let active = true;
    void request("/model-catalog", catalogStatusSchema)
      .then((status) => {
        if (active) setCatalogStatus(status);
      })
      .catch(() => {
        /* Optional catalog status. */
      });
    return () => {
      active = false;
    };
  }, []);

  async function refreshCatalog(): Promise<CatalogStatus | null> {
    setCatalogBusy(true);
    setCatalogError("");
    try {
      const next = await request("/model-catalog/refresh", catalogStatusSchema, {
        method: "POST",
      });
      setCatalogStatus(next);
      await onReloadModels();
      return next;
    } catch (failure) {
      setCatalogError(errorText(failure));
      return null;
    } finally {
      setCatalogBusy(false);
    }
  }

  const catalogNeedsUpdate = Boolean(
    catalogStatus && (!catalogStatus.cached || catalogStatus.stale),
  );

  return (
    <div className="settings-sections">
      {settings && (
        <DefaultModelsBlock initial={settings} models={enabledModels} onSaved={onSettingsSaved} />
      )}
      {catalogStatus && (
        <div className={`catalog-status-bar ${catalogNeedsUpdate ? "is-stale" : ""}`}>
          <div className="catalog-status-text">
            <strong>模型元信息缓存</strong>
            <span>
              {!catalogStatus.cached
                ? "尚未同步；联网更新后，导入模型时可自动预填上下文窗口、输出上限与推荐协议。"
                : catalogStatus.stale
                  ? `已缓存 ${catalogStatus.model_count} 个模型（更新于 ${formatCatalogDate(catalogStatus.fetched_at)}，已超过 ${catalogStatus.stale_after_days} 天，建议更新）`
                  : `已缓存 ${catalogStatus.model_count} 个模型 · 更新于 ${formatCatalogDate(catalogStatus.fetched_at)}（${catalogStatus.source}）`}
            </span>
          </div>
          <Button
            variant={catalogNeedsUpdate ? "primary" : "secondary"}
            disabled={catalogBusy}
            onClick={() => void refreshCatalog()}
          >
            <RefreshCw size={14} />
            {catalogBusy ? "更新中…" : catalogStatus.cached ? "更新目录" : "同步目录"}
          </Button>
        </div>
      )}
      <Alert>{catalogError}</Alert>
      <hr className="settings-divider" />
      {providers.length === 0 ? (
        <EmptyState title="尚未配置提供商">
          请先在「提供商」中添加并启用至少一个 API 连接。
        </EmptyState>
      ) : (
        <div className="settings-sections">
          {providers.map((provider, index) => (
            <div key={provider.id}>
              {index > 0 && <hr className="settings-divider" />}
              <ProviderModelsBlock
                provider={provider}
                models={models.filter((item) => item.provider_id === provider.id)}
                catalogStatus={catalogStatus}
                catalogBusy={catalogBusy}
                onRefreshCatalog={refreshCatalog}
                onReloadModels={onReloadModels}
                onSaved={onModelSaved}
                onDeleted={onModelDeleted}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DefaultModelsBlock({
  initial,
  models,
  onSaved,
}: {
  initial: GlobalSettings;
  models: Model[];
  onSaved: (settings: GlobalSettings) => void;
}) {
  const [primary, setPrimary] = useState(initial.default_model_id ?? "none");
  const [background, setBackground] = useState(initial.background_model_id ?? "none");
  const [saving, setSaving] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const [error, setError] = useState("");

  const isDirty =
    primary !== (initial.default_model_id ?? "none") ||
    background !== (initial.background_model_id ?? "none");

  const options = [
    { value: "none", label: "未设置" },
    ...models.map((model) => ({ value: model.id, label: model.display_name || model.model_id })),
  ];

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setSavedNotice(false);
    try {
      const result = await request("/settings", settingsSchema, {
        method: "PATCH",
        body: {
          default_model_id: primary === "none" ? null : primary,
          background_model_id: background === "none" ? null : background,
          global_rules_markdown: initial.global_rules_markdown,
        },
      });
      setSavedNotice(true);
      onSaved(result);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="settings-block" onSubmit={(event) => void save(event)}>
      <div className="settings-section-bar">
        <div>
          <h3>默认模型</h3>
          <p className="muted">主写作模型用于对话与创作；后台模型用于生成标题与压缩上下文。</p>
        </div>
        <div className="row">
          {savedNotice && !isDirty && <Badge tone="success">已保存</Badge>}
          <Button type="submit" variant="primary" disabled={saving || !isDirty}>
            {saving ? "保存中…" : "保存"}
          </Button>
        </div>
      </div>
      <div className="settings-defaults-bar">
        <Select
          label="主写作模型"
          value={primary}
          onChange={(v) => {
            setPrimary(v);
            setSavedNotice(false);
          }}
          options={options}
        />
        <Select
          label="后台轻量模型"
          value={background}
          onChange={(v) => {
            setBackground(v);
            setSavedNotice(false);
          }}
          options={options}
        />
      </div>
      {((primary !== "none" && !models.some((model) => model.id === primary)) ||
        (background !== "none" && !models.some((model) => model.id === background))) && (
        <Alert>所选默认模型已停用或删除，请重新选择。</Alert>
      )}
      <Alert>{error}</Alert>
    </form>
  );
}

function ProviderModelsBlock({
  provider,
  models,
  catalogStatus,
  catalogBusy,
  onRefreshCatalog,
  onReloadModels,
  onSaved,
  onDeleted,
}: {
  provider: Provider;
  models: Model[];
  catalogStatus: CatalogStatus | null;
  catalogBusy: boolean;
  onRefreshCatalog: () => Promise<CatalogStatus | null>;
  onReloadModels: () => Promise<void>;
  onSaved: (model: Model) => void;
  onDeleted: (id: string) => void;
}) {
  const [editing, setEditing] = useState<Model | "new" | null>(null);
  const [deleting, setDeleting] = useState<Model | null>(null);
  const [discovered, setDiscovered] = useState<Discovered[] | null>(null);
  const [discoverFilter, setDiscoverFilter] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function discover() {
    setBusy(true);
    setError("");
    try {
      const list = await request(`/providers/${provider.id}/discover`, discoveredSchema.array(), {
        method: "POST",
      });
      setDiscovered(list);
      setDiscoverFilter("");
      setSelected([]);
      await onReloadModels();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function importModels() {
    setBusy(true);
    setError("");
    try {
      for (const model of discovered ?? []) {
        if (
          !model.supported ||
          !selected.includes(model.model_id) ||
          models.some((item) => item.model_id === model.model_id)
        ) {
          continue;
        }
        const saved = await request(`/providers/${provider.id}/models`, modelSchema, {
          method: "POST",
          body: {
            model_id: model.model_id,
            display_name: model.display_name,
            protocol_override: null,
            protocol_mode: "auto",
            context_window: null,
            max_output_tokens: null,
            discovered_protocol: model.server_protocol,
            discovered_context_window: model.server_context_window,
            discovered_max_output_tokens: model.server_max_output_tokens,
          },
        });
        onSaved(saved);
      }
      setDiscovered(null);
      setSelected([]);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function resolveChange(
    model: Model,
    change: Model["pending_changes"][number],
    action: "accept" | "keep",
  ) {
    setBusy(true);
    setError("");
    try {
      const saved = await request(`/models/${model.id}/resolve-change`, modelSchema, {
        method: "POST",
        body: {
          field: change.field,
          action,
          expected_upstream_value: change.upstream_value,
          expected_upstream_source: change.upstream_source,
          expected_user_value: change.user_value,
        },
      });
      onSaved(saved);
    } catch (failure) {
      setError(errorText(failure));
      if (failure instanceof ApiError && failure.status === 409) await onReloadModels();
    } finally {
      setBusy(false);
    }
  }

  async function toggleModelEnabled(model: Model, nextEnabled: boolean) {
    setBusy(true);
    setError("");
    try {
      const saved = await request(`/models/${model.id}`, modelSchema, {
        method: "PATCH",
        body: { enabled: nextEnabled },
      });
      onSaved(saved);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function deleteModel() {
    if (!deleting) return;
    setBusy(true);
    setError("");
    try {
      await remove(`/models/${deleting.id}`);
      onDeleted(deleting.id);
      setDeleting(null);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  const filteredDiscovered = useMemo(() => {
    if (!discovered) return [];
    const q = discoverFilter.trim().toLowerCase();
    return q
      ? discovered.filter(
          (m) => m.model_id.toLowerCase().includes(q) || m.display_name.toLowerCase().includes(q),
        )
      : discovered;
  }, [discovered, discoverFilter]);

  return (
    <section className="settings-block">
      <div className="settings-section-bar">
        <div className="row">
          <span
            className={`provider-dot ${provider.enabled ? "" : "is-disabled"}`}
            aria-hidden="true"
          />
          <h3>{provider.name}</h3>
          {!provider.enabled && <span className="muted">· 已停用</span>}
        </div>
        <div className="row">
          <Button disabled={busy} onClick={() => void discover()}>
            <RefreshCw size={14} />
            发现模型
          </Button>
          <Button onClick={() => setEditing("new")}>
            <Plus size={14} />
            手动添加
          </Button>
        </div>
      </div>

      <Alert>{error}</Alert>

      {models.length === 0 ? (
        <p className="muted">尚未导入模型，点击右上角「发现模型」或「手动添加」。</p>
      ) : (
        <div className="flat-list">
          {models.map((model) => {
            const shownProtocol = model.effective_protocol_override ?? model.protocol_override;
            const shownContext = model.effective_context_window ?? model.context_window;
            return (
              <div className="flat-list-group" key={model.id}>
                <div className="flat-list-row">
                  <div className="flat-list-main">
                    <div className="row">
                      <strong>{model.display_name || model.model_id}</strong>
                      {shownProtocol && <Badge>{shownProtocol}</Badge>}
                    </div>
                    <span className="muted">
                      {model.model_id}
                      {shownContext ? ` · ${shownContext.toLocaleString()} ctx` : ""}
                    </span>
                  </div>
                  <div className="row">
                    <Switch
                      checked={model.enabled}
                      disabled={busy}
                      label={`${model.enabled ? "停用" : "启用"} ${model.display_name || model.model_id}`}
                      onCheckedChange={(checked) => void toggleModelEnabled(model, checked)}
                    />
                    <Button
                      variant="ghost"
                      className="icon-control"
                      title="编辑模型"
                      aria-label={`编辑模型 ${model.model_id}`}
                      onClick={() => setEditing(model)}
                    >
                      <Pencil size={14} />
                    </Button>
                    <Button
                      variant="ghost"
                      className="icon-control"
                      title="删除模型"
                      aria-label={`删除模型 ${model.model_id}`}
                      onClick={() => setDeleting(model)}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                </div>
                {model.pending_changes.length > 0 && (
                  <div className="model-upstream-changes">
                    {model.pending_changes.map((change) => (
                      <div className="model-upstream-change-row" key={change.field}>
                        <span>
                          {formatParamField(change.field)}：
                          {formatUpstreamSource(change.upstream_source)}
                          已更新为 {formatParamValue(change.field, change.upstream_value)}
                          （当前手填 {formatParamValue(change.field, change.user_value)}）
                        </span>
                        <div className="row">
                          <Button
                            variant="ghost"
                            disabled={busy}
                            onClick={() => void resolveChange(model, change, "accept")}
                          >
                            采用最新
                          </Button>
                          <Button
                            variant="ghost"
                            disabled={busy}
                            onClick={() => void resolveChange(model, change, "keep")}
                          >
                            保留当前
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <Dialog
        open={discovered !== null}
        onOpenChange={(value) => {
          if (!value && !busy) setDiscovered(null);
        }}
        title={`发现模型 · ${provider.name}`}
      >
        <div className="stack">
          {catalogStatus && (!catalogStatus.cached || catalogStatus.stale) && (
            <div className="discovery-catalog-hint">
              <span>
                {!catalogStatus.cached
                  ? "同步模型元信息缓存后，可自动预填上下文窗口与推荐协议。"
                  : "本地模型元信息缓存已过期，建议更新后再导入。"}
              </span>
              <Button
                variant="ghost"
                disabled={busy || catalogBusy}
                onClick={() => {
                  void onRefreshCatalog().then((next) => {
                    if (next) void discover();
                  });
                }}
              >
                <RefreshCw size={12} />
                {catalogBusy ? "更新中…" : catalogStatus.cached ? "更新并刷新" : "立即同步"}
              </Button>
            </div>
          )}
          <Input
            placeholder="筛选模型 ID 或名称…"
            aria-label="筛选发现的模型"
            value={discoverFilter}
            onChange={(event) => setDiscoverFilter(event.target.value)}
          />
          <div className="row spread">
            <span className="muted">共发现 {discovered?.length ?? 0} 个模型</span>
            <div className="row">
              <Button
                variant="ghost"
                disabled={busy || !discovered}
                onClick={() =>
                  setSelected(
                    (discovered ?? [])
                      .filter(
                        (model) =>
                          model.supported &&
                          !models.some((item) => item.model_id === model.model_id),
                      )
                      .map((model) => model.model_id),
                  )
                }
              >
                全选新模型
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => setSelected([])}>
                清空
              </Button>
            </div>
          </div>
          <div className="discovery-modal-list">
            {filteredDiscovered.map((model) => {
              const exists = models.some((item) => item.model_id === model.model_id);
              const checked = selected.includes(model.model_id);
              const metaParts = [model.model_id];
              if (model.context_window) {
                metaParts.push(`${formatCompactTokens(model.context_window)} ctx`);
              }
              if (model.max_output_tokens) {
                metaParts.push(`${formatCompactTokens(model.max_output_tokens)} out`);
              }
              if (model.catalog_match === "cross_provider" && model.catalog_provider) {
                metaParts.push(
                  `参考 ${model.catalog_provider}${model.catalog_stale ? "（缓存已过期）" : ""}`,
                );
              } else if (model.catalog_match === "provider" && model.catalog_stale) {
                metaParts.push("缓存已过期");
              }
              return (
                <button
                  type="button"
                  key={model.model_id}
                  disabled={exists || !model.supported || busy}
                  className={`discovery-pick-item ${checked ? "is-checked" : ""}`}
                  title={!model.supported ? model.unsupported_reason || "不支持接入" : undefined}
                  onClick={() =>
                    setSelected((items) =>
                      items.includes(model.model_id)
                        ? items.filter((item) => item !== model.model_id)
                        : [...items, model.model_id],
                    )
                  }
                >
                  <span className="discovery-check" aria-hidden="true">
                    {exists || checked ? <Check size={13} /> : null}
                  </span>
                  <div className="discovery-pick-text">
                    <strong>{model.display_name || model.model_id}</strong>
                    <span>{metaParts.join(" · ")}</span>
                  </div>
                  <div className="discovery-pick-badges">
                    {model.recommended_protocol && <Badge>{model.recommended_protocol}</Badge>}
                    {!model.supported && <Badge tone="warning">不支持接入</Badge>}
                    {exists && <Badge>已导入</Badge>}
                  </div>
                </button>
              );
            })}
            {filteredDiscovered.length === 0 && <p className="muted">没有匹配的模型</p>}
          </div>
          <div className="dialog-actions">
            <Button disabled={busy} onClick={() => setDiscovered(null)}>
              关闭
            </Button>
            <Button
              variant="primary"
              disabled={busy || selected.length === 0}
              onClick={() => void importModels()}
            >
              导入选中（{selected.length}）
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={editing !== null}
        onOpenChange={(value) => {
          if (!value) setEditing(null);
        }}
        title={editing === "new" ? "添加模型" : "编辑模型"}
        className="model-dialog"
      >
        {editing !== null && (
          <ModelEditor
            providerId={provider.id}
            providerPreset={provider.preset}
            catalogCached={Boolean(catalogStatus?.cached)}
            initial={editing === "new" ? undefined : editing}
            onSaved={(model) => {
              onSaved(model);
              setEditing(null);
            }}
            onCancel={() => setEditing(null)}
          />
        )}
      </Dialog>

      <Dialog
        open={deleting !== null}
        onOpenChange={(value) => {
          if (!value && !busy) setDeleting(null);
        }}
        title="删除模型"
      >
        <p>确定删除「{deleting?.display_name || deleting?.model_id}」？</p>
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setDeleting(null)}>
            取消
          </Button>
          <Button disabled={busy} variant="danger" onClick={() => void deleteModel()}>
            确认删除
          </Button>
        </div>
      </Dialog>
    </section>
  );
}

function optionalPositive(value: string): number | null {
  if (!value.trim()) return null;
  return z.number().int().positive().parse(Number(value));
}

function ModelEditor({
  providerId,
  providerPreset,
  catalogCached,
  initial,
  onSaved,
  onCancel,
}: {
  providerId: string;
  providerPreset: string;
  catalogCached: boolean;
  initial?: Model;
  onSaved: (model: Model) => void;
  onCancel: () => void;
}) {
  const [modelId, setModelId] = useState(initial?.model_id ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const initialProtocol =
    initial?.protocol_mode === "auto"
      ? "auto"
      : (initial?.protocol_override ?? (initial ? "inherit" : "auto"));
  const [protocol, setProtocol] = useState(initialProtocol);
  const modelIdRef = useRef(initial?.model_id ?? "");
  const [context, setContext] = useState(initial?.context_window?.toString() ?? "");
  const [output, setOutput] = useState(initial?.max_output_tokens?.toString() ?? "");
  const [catalogSuggestion, setCatalogSuggestion] = useState<CatalogLookup | null>(null);
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [lookupBusy, setLookupBusy] = useState(false);
  const [lookupNote, setLookupNote] = useState("");
  const [error, setError] = useState("");

  async function fillFromCatalog() {
    const trimmed = modelId.trim();
    if (!trimmed || lookupBusy) return;
    setLookupBusy(true);
    setLookupNote("");
    try {
      const params = new URLSearchParams({
        model_id: trimmed,
        preset: providerPreset,
      });
      const result = await request(
        `/model-catalog/lookup?${params.toString()}`,
        catalogLookupSchema,
      );
      if (modelIdRef.current.trim() !== trimmed) return;
      if (!result.found) {
        setLookupNote("本地缓存未收录此型号，可手动填写或先更新目录。");
        return;
      }
      if (result.display_name)
        setDisplayName((current) => (current.trim() ? current : result.display_name));
      setCatalogSuggestion(result);
      const sourceLabel =
        result.match_scope === "cross_provider"
          ? `（跨提供商参考：${result.source_provider ?? "目录"}，未覆盖协议）`
          : result.source_provider
            ? `（来源：${result.source_provider}）`
            : "";
      setLookupNote(
        `已读取缓存参数，留空的数字与自动协议随来源更新${sourceLabel}${result.stale ? "，缓存已过期建议更新" : ""}。`,
      );
    } catch (failure) {
      setLookupNote(errorText(failure));
    } finally {
      setLookupBusy(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const saved = await request(
        initial ? `/models/${initial.id}` : `/providers/${providerId}/models`,
        modelSchema,
        {
          method: initial ? "PATCH" : "POST",
          body: {
            ...(initial ? {} : { model_id: modelId.trim() }),
            display_name: displayName.trim() || modelId.trim(),
            ...(!initial || protocol !== initialProtocol
              ? {
                  protocol_mode:
                    protocol === "auto" || protocol === "inherit" ? protocol : "override",
                  protocol_override:
                    protocol === "auto" || protocol === "inherit"
                      ? null
                      : protocolSchema.parse(protocol),
                }
              : {}),
            ...(!initial || optionalPositive(context) !== initial.context_window
              ? { context_window: optionalPositive(context) }
              : {}),
            ...(!initial || optionalPositive(output) !== initial.max_output_tokens
              ? { max_output_tokens: optionalPositive(output) }
              : {}),
            enabled,
          },
        },
      );
      onSaved(saved);
    } catch (failure) {
      setError(
        failure instanceof z.ZodError
          ? "上下文与输出上限应为正整数，或留空自动采用来源值。"
          : errorText(failure),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="model-form" onSubmit={(event) => void save(event)}>
      <div className="stack model-form-scroll">
        <div className="model-id-row">
          <Input
            label="模型 ID"
            value={modelId}
            disabled={!!initial}
            onChange={(event) => {
              modelIdRef.current = event.target.value;
              setCatalogSuggestion(null);
              setModelId(event.target.value);
              setLookupNote("");
            }}
            required
          />
          {catalogCached && (
            <Button
              disabled={busy || lookupBusy || !modelId.trim()}
              onClick={() => void fillFromCatalog()}
            >
              {lookupBusy ? "查询中…" : "从缓存补全"}
            </Button>
          )}
        </div>
        {lookupNote && <p className="muted">{lookupNote}</p>}
        <Input
          label="显示名称"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
        />
        <Select
          label="单模型协议覆盖"
          value={protocol}
          onChange={(value) => {
            setProtocol(value);
          }}
          options={[
            {
              value: "auto",
              label:
                initial?.upstream_protocol || catalogSuggestion?.recommended_protocol
                  ? `自动（${initial?.upstream_protocol ?? catalogSuggestion?.recommended_protocol}）`
                  : "自动选择协议",
            },
            { value: "inherit", label: "继承提供商默认协议" },
            ...protocols,
          ]}
        />
        <div className="settings-grid-2">
          <Input
            label="上下文窗口（可选）"
            type="number"
            min={1}
            step={1}
            placeholder={
              initial?.upstream_context_window || catalogSuggestion?.context_window
                ? `自动：${initial?.upstream_context_window ?? catalogSuggestion?.context_window}`
                : "自动"
            }
            value={context}
            onChange={(event) => setContext(event.target.value)}
          />
          <Input
            label="最大输出 tokens（可选）"
            type="number"
            min={1}
            step={1}
            placeholder={
              initial?.upstream_max_output_tokens || catalogSuggestion?.max_output_tokens
                ? `自动：${initial?.upstream_max_output_tokens ?? catalogSuggestion?.max_output_tokens}`
                : "自动"
            }
            value={output}
            onChange={(event) => setOutput(event.target.value)}
          />
        </div>
        <div className="switch-row">
          <span>启用此模型</span>
          <Switch checked={enabled} label="启用此模型" onCheckedChange={setEnabled} />
        </div>
        <Alert>{error}</Alert>
      </div>
      <div className="dialog-actions model-form-footer">
        <Button disabled={busy} onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? "保存中…" : "保存模型"}
        </Button>
      </div>
    </form>
  );
}

function RulesSection({
  settings,
  activeBook,
  books,
  onSettingsSaved,
  onBookSaved,
}: {
  settings: GlobalSettings;
  activeBook: Book | null;
  books: Book[];
  onSettingsSaved: (settings: GlobalSettings) => void;
  onBookSaved: () => void;
}) {
  const [globalRules, setGlobalRules] = useState(settings.global_rules_markdown);
  const [savingGlobal, setSavingGlobal] = useState(false);
  const [savedGlobal, setSavedGlobal] = useState(false);
  const [globalError, setGlobalError] = useState("");

  const [bookId, setBookId] = useState(activeBook?.id ?? books[0]?.id ?? "");
  const currentBook = books.find((item) => item.id === bookId) ?? activeBook ?? books[0] ?? null;
  const [bookRules, setBookRules] = useState(currentBook?.rules_markdown ?? "");
  const [savingBook, setSavingBook] = useState(false);
  const [savedBook, setSavedBook] = useState(false);
  const [bookError, setBookError] = useState("");

  useEffect(() => {
    if (!currentBook) return;
    setBookRules(currentBook.rules_markdown);
    setSavedBook(false);
  }, [currentBook]);

  const globalDirty = globalRules !== settings.global_rules_markdown;
  const bookDirty = Boolean(currentBook && bookRules !== currentBook.rules_markdown);

  async function saveGlobalRules(event: FormEvent) {
    event.preventDefault();
    setSavingGlobal(true);
    setGlobalError("");
    setSavedGlobal(false);
    try {
      const result = await request("/settings", settingsSchema, {
        method: "PATCH",
        body: {
          default_model_id: settings.default_model_id,
          background_model_id: settings.background_model_id,
          global_rules_markdown: globalRules,
        },
      });
      setSavedGlobal(true);
      onSettingsSaved(result);
    } catch (failure) {
      setGlobalError(errorText(failure));
    } finally {
      setSavingGlobal(false);
    }
  }

  async function saveBookRules(event: FormEvent) {
    event.preventDefault();
    if (!currentBook) return;
    setSavingBook(true);
    setBookError("");
    setSavedBook(false);
    try {
      await request(`/books/${currentBook.id}`, bookSchema, {
        method: "PATCH",
        body: { rules_markdown: bookRules },
      });
      setSavedBook(true);
      onBookSaved();
    } catch (failure) {
      setBookError(errorText(failure));
    } finally {
      setSavingBook(false);
    }
  }

  return (
    <div className="settings-sections">
      <form className="settings-block" onSubmit={(event) => void saveGlobalRules(event)}>
        <div className="settings-section-bar">
          <div>
            <h3>全局写作规则</h3>
            <p className="muted">对所有作品生效，用于约定个人文风偏好、忌用表达或通用叙事习惯。</p>
          </div>
          <div className="row">
            {savedGlobal && !globalDirty && <Badge tone="success">已保存</Badge>}
            <Button type="submit" variant="primary" disabled={savingGlobal || !globalDirty}>
              {savingGlobal ? "保存中…" : "保存"}
            </Button>
          </div>
        </div>
        <Textarea
          aria-label="全局写作规则"
          rows={6}
          value={globalRules}
          onChange={(event) => {
            setGlobalRules(event.target.value);
            setSavedGlobal(false);
          }}
          placeholder="记录个人文风偏好、忌用表达或通用叙事约束…"
        />
        <Alert>{globalError}</Alert>
      </form>

      <hr className="settings-divider" />

      <form className="settings-block" onSubmit={(event) => void saveBookRules(event)}>
        <div className="settings-section-bar">
          <div>
            <h3>单书写作规则</h3>
            <p className="muted">仅对选中的作品生效，用于补充人称视角、时代口吻或本书特殊禁忌。</p>
          </div>
          <div className="row">
            {books.length > 0 && currentBook && (
              <div className="settings-book-picker">
                <Select
                  label="目标作品"
                  compact
                  value={currentBook.id}
                  onChange={(value) => {
                    setBookId(value);
                    const picked = books.find((item) => item.id === value);
                    if (picked) setBookRules(picked.rules_markdown);
                    setSavedBook(false);
                  }}
                  options={books.map((item) => ({ value: item.id, label: item.title }))}
                />
              </div>
            )}
            {savedBook && !bookDirty && <Badge tone="success">已保存</Badge>}
            <Button
              type="submit"
              variant="primary"
              disabled={savingBook || !currentBook || !bookDirty}
            >
              {savingBook ? "保存中…" : "保存"}
            </Button>
          </div>
        </div>
        {currentBook ? (
          <Textarea
            aria-label="单书写作规则"
            rows={6}
            value={bookRules}
            onChange={(event) => {
              setBookRules(event.target.value);
              setSavedBook(false);
            }}
            placeholder="记录本书专属的人称视角、时代口吻或特殊禁忌…"
          />
        ) : (
          <p className="muted">暂无作品，请先在书架新建作品。</p>
        )}
        <Alert>{bookError}</Alert>
      </form>
    </div>
  );
}

function PromptEditor({ initial, onSaved }: { initial: Prompt[]; onSaved: () => void }) {
  const [mode, setMode] = useState<"discuss" | "write" | "review">("discuss");
  const [prompts, setPrompts] = useState(initial);
  const [savedSnapshot, setSavedSnapshot] = useState<Record<string, string>>(() =>
    Object.fromEntries(initial.map((item) => [item.mode, item.markdown])),
  );
  const [busy, setBusy] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const [error, setError] = useState("");
  const [reset, setReset] = useState(false);

  const current = prompts.find((item) => item.mode === mode);
  const activeMeta = modeMeta.find((item) => item.value === mode) ?? modeMeta[0];
  const isDirty = Boolean(current && current.markdown !== (savedSnapshot[mode] ?? ""));

  async function save(restore: boolean) {
    if (!current) return;
    setBusy(true);
    setError("");
    setSavedNotice(false);
    try {
      const saved = await request(`/settings/prompts/${current.mode}`, promptSchema, {
        method: restore ? "DELETE" : "PUT",
        ...(restore ? {} : { body: { markdown: current.markdown } }),
      });
      setPrompts((items) => items.map((item) => (item.mode === saved.mode ? saved : item)));
      setSavedSnapshot((prev) => ({ ...prev, [saved.mode]: saved.markdown }));
      setReset(false);
      setSavedNotice(true);
      onSaved();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="settings-block prompts-editor-full"
      onSubmit={(event) => {
        event.preventDefault();
        void save(false);
      }}
    >
      <div className="settings-section-bar">
        <div className="segmented-tabs" role="tablist" aria-label="选择写作模式">
          {modeMeta.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.value}
                type="button"
                role="tab"
                aria-selected={mode === item.value}
                className={`segmented-tab ${mode === item.value ? "is-active" : ""}`}
                onClick={() => {
                  setMode(item.value);
                  setError("");
                  setSavedNotice(false);
                }}
              >
                <Icon size={14} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>
        <div className="row">
          {savedNotice && !isDirty && <Badge tone="success">已保存</Badge>}
          <Button disabled={busy || !current} onClick={() => setReset(true)}>
            <RotateCcw size={14} />
            恢复默认
          </Button>
          <Button type="submit" variant="primary" disabled={busy || !current || !isDirty}>
            {busy ? "保存中…" : "保存"}
          </Button>
        </div>
      </div>

      <p className="muted">{activeMeta.toolsHint}</p>

      {current && (
        <Textarea
          aria-label={`${activeMeta.label}提示词`}
          className="prompts-textarea"
          value={current.markdown}
          onChange={(event) => {
            setPrompts((items) =>
              items.map((item) =>
                item.mode === mode ? { ...item, markdown: event.target.value } : item,
              ),
            );
            setSavedNotice(false);
          }}
        />
      )}
      <Alert>{error}</Alert>

      <Dialog
        open={reset}
        onOpenChange={(value) => {
          if (!busy) setReset(value);
        }}
        title="恢复默认提示词"
        description={`确定将「${activeMeta.label}」恢复为默认提示词吗？`}
      >
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setReset(false)}>
            取消
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => void save(true)}>
            确认恢复
          </Button>
        </div>
      </Dialog>
    </form>
  );
}

function EditorPrefsSection() {
  const [prefs, setPrefs] = useState<EditorPrefs>(() => loadEditorPrefs());

  function update(patch: Partial<EditorPrefs>) {
    const next = { ...prefs, ...patch };
    setPrefs(next);
    saveEditorPrefs(next);
  }

  return (
    <div className="settings-sections">
      <div className="settings-section-bar">
        <div>
          <h3>排版与阅读偏好</h3>
          <p className="muted">即时应用于当前设备上的正文与设定编辑器，无需额外保存。</p>
        </div>
        <Button
          variant="ghost"
          onClick={() => {
            setPrefs(DEFAULT_EDITOR_PREFS);
            saveEditorPrefs(DEFAULT_EDITOR_PREFS);
          }}
        >
          <RotateCcw size={14} />
          恢复默认
        </Button>
      </div>

      <div className="pref-row">
        <div>
          <strong>正文字体</strong>
          <p className="muted">长篇写作推荐衬线体，梳理设定可选无衬线体。</p>
        </div>
        <div className="segmented-tabs">
          <button
            type="button"
            className={`segmented-tab ${prefs.fontFamily === "serif" ? "is-active" : ""}`}
            onClick={() => update({ fontFamily: "serif" })}
          >
            衬线体
          </button>
          <button
            type="button"
            className={`segmented-tab ${prefs.fontFamily === "sans" ? "is-active" : ""}`}
            onClick={() => update({ fontFamily: "sans" })}
          >
            无衬线体
          </button>
        </div>
      </div>

      <div className="pref-row">
        <div>
          <strong>正文字号</strong>
          <p className="muted">当前 {prefs.fontSize} px</p>
        </div>
        <div className="segmented-tabs">
          {[14, 15, 16, 18, 20].map((size) => (
            <button
              key={size}
              type="button"
              className={`segmented-tab ${prefs.fontSize === size ? "is-active" : ""}`}
              onClick={() => update({ fontSize: size })}
            >
              {size}
            </button>
          ))}
        </div>
      </div>

      <div className="pref-row">
        <div>
          <strong>正文行高</strong>
          <p className="muted">调整段落呼吸感（当前 {prefs.lineHeight}）</p>
        </div>
        <div className="segmented-tabs">
          {[
            { value: 1.75, label: "紧凑" },
            { value: 2.1, label: "适中" },
            { value: 2.4, label: "宽松" },
          ].map((option) => (
            <button
              key={option.value}
              type="button"
              className={`segmented-tab ${prefs.lineHeight === option.value ? "is-active" : ""}`}
              onClick={() => update({ lineHeight: option.value })}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div className="pref-row">
        <div>
          <strong>正文栏宽</strong>
          <p className="muted">控制单行阅读宽度。</p>
        </div>
        <div className="segmented-tabs">
          {[
            { value: "narrow" as const, label: "窄栏" },
            { value: "medium" as const, label: "标准" },
            { value: "wide" as const, label: "宽栏" },
          ].map((option) => (
            <button
              key={option.value}
              type="button"
              className={`segmented-tab ${prefs.maxWidth === option.value ? "is-active" : ""}`}
              onClick={() => update({ maxWidth: option.value })}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function DataServicePanel({
  activeBook,
  books,
  onBookImported,
}: {
  activeBook: Book | null;
  books: Book[];
  onBookImported: () => void;
}) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [copiedPath, setCopiedPath] = useState(false);
  const [exportBookId, setExportBookId] = useState(activeBook?.id ?? books[0]?.id ?? "");
  const [deletingBackup, setDeletingBackup] = useState<BackupRecord | null>(null);
  const importRef = useRef<HTMLInputElement | null>(null);

  const selectedExportBook =
    books.find((item) => item.id === exportBookId) ?? activeBook ?? books[0] ?? null;

  async function reload() {
    setLoading(true);
    setError("");
    try {
      setStatus(await request("/system/status", systemStatusSchema));
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  async function createBackup() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const record = await request("/backups", backupRecordSchema, { method: "POST" });
      setMessage(`已备份：${record.name}`);
      await reload();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function confirmDeleteBackup() {
    if (!deletingBackup) return;
    setBusy(true);
    setError("");
    try {
      await remove(`/backups/${encodeURIComponent(deletingBackup.name)}`);
      setDeletingBackup(null);
      await reload();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function importZip(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const imported = await upload("/books/import", bookSchema, file, "POST", "application/zip");
      setMessage(`已导入作品「${imported.title}」`);
      setExportBookId(imported.id);
      onBookImported();
      await reload();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  function downloadBook(bookId: string, format: "zip" | "md" | "txt") {
    const link = document.createElement("a");
    link.href = `/api/books/${bookId}/export?format=${format}`;
    link.download = "";
    link.click();
  }

  function downloadBackup(name: string) {
    const link = document.createElement("a");
    link.href = `/api/backups/${encodeURIComponent(name)}`;
    link.download = name;
    link.click();
  }

  async function copyDataDir(dir: string) {
    try {
      await navigator.clipboard.writeText(dir);
      setCopiedPath(true);
      window.setTimeout(() => setCopiedPath(false), 1800);
    } catch {
      /* Clipboard unavailable. */
    }
  }

  return (
    <div className="settings-sections">
      <Alert>{error}</Alert>
      {message && (
        <div className="badge badge-success" role="status">
          {message}
        </div>
      )}

      <section className="settings-block">
        <div className="settings-section-bar">
          <div>
            <h3>作品导入与导出</h3>
            <p className="muted">选择任意作品导出完整 ZIP 包或正文合集，也可导入外部作品包。</p>
          </div>
          <div>
            <input
              ref={importRef}
              type="file"
              accept=".zip,application/zip"
              hidden
              onChange={(event) => {
                void importZip(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
            <Button disabled={busy} onClick={() => importRef.current?.click()}>
              <Upload size={14} />
              导入 ZIP 作品
            </Button>
          </div>
        </div>
        {books.length > 0 && selectedExportBook ? (
          <div className="data-export-bar">
            <div className="settings-book-picker">
              <Select
                label="选择导出作品"
                compact
                value={selectedExportBook.id}
                onChange={setExportBookId}
                options={books.map((item) => ({ value: item.id, label: item.title }))}
              />
            </div>
            <div className="row">
              <Button onClick={() => downloadBook(selectedExportBook.id, "zip")}>
                <Download size={14} />
                ZIP 完整包
              </Button>
              <Button onClick={() => downloadBook(selectedExportBook.id, "md")}>
                <Download size={14} />
                Markdown
              </Button>
              <Button onClick={() => downloadBook(selectedExportBook.id, "txt")}>
                <Download size={14} />
                TXT
              </Button>
            </div>
          </div>
        ) : (
          <p className="muted">暂无可导出的作品。</p>
        )}
      </section>

      <hr className="settings-divider" />

      <section className="settings-block">
        <div className="settings-section-bar">
          <div>
            <h3>本地完整备份</h3>
            <p className="muted">将全部作品数据、对话与封面附件打包为独立 ZIP 备份文件。</p>
          </div>
          <Button variant="primary" disabled={busy} onClick={() => void createBackup()}>
            <HardDrive size={14} />
            {busy ? "备份中…" : "立即备份"}
          </Button>
        </div>
        {status && status.backups.length > 0 ? (
          <div className="flat-list">
            {status.backups.map((item) => (
              <div className="flat-list-row" key={item.name}>
                <div className="flat-list-main">
                  <div className="row">
                    <strong>{item.name}</strong>
                    <Badge>{item.includes_attachments ? "完整备份" : "旧版数据库"}</Badge>
                  </div>
                  <span className="muted">
                    {formatBytes(item.size_bytes)} ·{" "}
                    {new Date(item.created_at).toLocaleString("zh-CN")}
                  </span>
                </div>
                <div className="row">
                  <Button
                    variant="ghost"
                    className="icon-control"
                    title="下载备份"
                    aria-label={`下载备份 ${item.name}`}
                    onClick={() => downloadBackup(item.name)}
                  >
                    <Download size={14} />
                  </Button>
                  <Button
                    variant="ghost"
                    className="icon-control"
                    title="删除备份"
                    aria-label={`删除备份 ${item.name}`}
                    disabled={busy}
                    onClick={() => setDeletingBackup(item)}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">暂无备份文件</p>
        )}
      </section>

      <hr className="settings-divider" />

      <section className="settings-block">
        <div className="settings-section-bar">
          <div>
            <h3>本机服务与存储</h3>
            <p className="muted">当前服务运行状态及数据文件存放位置。</p>
          </div>
          <Button disabled={loading || busy} onClick={() => void reload()}>
            <RefreshCw size={14} />
            刷新
          </Button>
        </div>
        {loading ? (
          <p className="muted">正在读取状态…</p>
        ) : (
          status && (
            <div className="data-status-grid">
              <div>
                <span className="muted">服务地址</span>
                <strong>
                  http://{status.host}:{status.port}
                </strong>
              </div>
              <div>
                <span className="muted">内容规模</span>
                <strong>
                  {status.books} 部作品 · {status.sessions} 段对话
                </strong>
              </div>
              <div>
                <span className="muted">数据占用</span>
                <strong>
                  {formatBytes(status.database_bytes + status.wal_bytes + status.attachments_bytes)}
                </strong>
              </div>
              <div>
                <span className="muted">数据结构版本</span>
                <strong>v{status.schema_version}</strong>
              </div>
              <div className="data-status-wide">
                <div className="row spread">
                  <span className="muted">数据目录</span>
                  <button
                    type="button"
                    className="copy-path-btn"
                    onClick={() => void copyDataDir(status.data_dir)}
                  >
                    {copiedPath ? <Check size={12} /> : <Copy size={12} />}
                    <span>{copiedPath ? "已复制" : "复制路径"}</span>
                  </button>
                </div>
                <code>{status.data_dir}</code>
              </div>
            </div>
          )
        )}
      </section>

      <Dialog
        open={deletingBackup !== null}
        onOpenChange={(value) => {
          if (!value && !busy) setDeletingBackup(null);
        }}
        title="删除备份"
        description={`确定删除备份文件「${deletingBackup?.name ?? ""}」吗？此操作无法撤销。`}
      >
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setDeletingBackup(null)}>
            取消
          </Button>
          <Button variant="danger" disabled={busy} onClick={() => void confirmDeleteBackup()}>
            确认删除
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
