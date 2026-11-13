import { Component, onMounted, proxy, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

export class Workbench extends Component {
    static template = "microscope_office_mvp.Workbench";
    props = useProps({ ...standardActionServiceProps });

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = proxy({
            loading: true,
            overview: null,
            loadError: false,
            opening: false,
            failedAction: null,
            failedTitle: "",
        });
        onMounted(() => {
            this.loadOverview();
        });
    }

    get primaryShortcuts() {
        return (this.state.overview?.shortcuts || []).filter(
            (shortcut) => shortcut.key === "quote" || shortcut.key === "meeting"
        );
    }

    get otherShortcuts() {
        return (this.state.overview?.shortcuts || []).filter(
            (shortcut) => shortcut.key !== "quote" && shortcut.key !== "meeting"
        );
    }

    get cards() {
        return this.state.overview?.cards || [];
    }

    async loadOverview() {
        this.state.loading = true;
        this.state.loadError = false;
        try {
            this.state.overview = await this.orm.call("mops.workbench", "get_overview", []);
        } catch {
            this.state.loadError = true;
        } finally {
            this.state.loading = false;
        }
    }

    async openAction(action, title) {
        if (this.state.opening) {
            return;
        }
        this.state.opening = true;
        this.state.failedAction = null;
        this.state.failedTitle = "";
        try {
            await this.action.doAction(action);
        } catch {
            this.state.failedAction = action;
            this.state.failedTitle = title;
        } finally {
            this.state.opening = false;
        }
    }

    retryAction() {
        return this.openAction(this.state.failedAction, this.state.failedTitle);
    }
}

registry.category("actions").add("mops_workbench", Workbench);
