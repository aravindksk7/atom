(function (global) {
  'use strict';
  global.ETL_FEATURE_FILE_SERVERS = function () {
    return {
      fileServers: [],
      fileServerModal: { show: false, editingId: null, data: {} },
      fileServerTestResult: {},

      async loadFileServers() {
        try {
          this.fileServers = await api('GET', '/api/file-servers');
        } catch (e) {
          this.toast('error', 'Failed to load file servers', e.message);
        }
      },

      openFileServerModal(profile) {
        this.fileServerModal = {
          show: true,
          editingId: profile ? profile.id : null,
          data: profile ? { ...profile } : { kind: 'sftp', port: 22, auth_method: 'password' },
        };
        this.onFileServerKindChange();
      },

      // port is shared across kinds: swap the sftp default (22) and the smb
      // default (445) when the kind changes, leaving any custom port alone.
      // SMB profiles saved before smb had a port field carry 22, shown as 445.
      onFileServerKindChange() {
        const d = this.fileServerModal.data;
        const port = Number(d.port);
        if (d.kind === 'smb' && (!port || port === 22)) d.port = 445;
        else if ((d.kind === 'sftp' || d.kind === 'scp') && (!port || port === 445)) d.port = 22;
      },

      async saveFileServer() {
        const m = this.fileServerModal;
        try {
          if (m.editingId) {
            await api('PUT', `/api/file-servers/${m.editingId}`, m.data);
          } else {
            await api('POST', '/api/file-servers', m.data);
          }
          this.fileServerModal.show = false;
          await this.loadFileServers();
          this.toast('success', 'File server saved');
        } catch (e) {
          this.toast('error', 'Save failed', e.message);
        }
      },

      async deleteFileServer(id) {
        try {
          await api('DELETE', `/api/file-servers/${id}`);
          await this.loadFileServers();
          this.toast('success', 'File server deleted');
        } catch (e) {
          this.toast('error', 'Delete failed', e.message);
        }
      },

      async testFileServer(id, acceptFingerprint) {
        try {
          const result = await api('POST', `/api/file-servers/${id}/test`, { accept_fingerprint: Boolean(acceptFingerprint) });
          this.fileServerTestResult = { ...this.fileServerTestResult, [id]: result };
          if (result.status === 'ok') this.toast('success', 'Connection OK');
        } catch (e) {
          this.toast('error', 'Test failed', e.message);
        }
      },
    };
  };
})(window);
