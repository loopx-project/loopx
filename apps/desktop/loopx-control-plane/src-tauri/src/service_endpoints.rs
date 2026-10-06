use std::net::TcpListener;

use crate::services::ServiceKind;

/// A native window keeps one endpoint pair across runtime repairs. Release
/// windows own their services; Vite retains the CLI ports its proxy expects.
#[derive(Clone, Copy)]
pub(crate) struct ServiceEndpoints {
    status: u16,
    chat: u16,
    pub isolated: bool,
}

impl ServiceEndpoints {
    pub fn allocate(isolated: bool) -> std::io::Result<Self> {
        if !isolated {
            return Ok(Self {
                status: 8766,
                chat: 8767,
                isolated,
            });
        }
        // Keep both reservations until their distinct ports have been chosen.
        // The CLI owns binding; an intervening listener fails closed at start.
        let status = reserve_private_endpoint()?;
        let chat = reserve_private_endpoint()?;
        Ok(Self {
            status: status.local_addr()?.port(),
            chat: chat.local_addr()?.port(),
            isolated,
        })
    }

    pub fn port(self, kind: ServiceKind) -> u16 {
        match kind {
            ServiceKind::Status => self.status,
            ServiceKind::Chat => self.chat,
        }
    }

    #[cfg(any(not(dev), test))]
    pub fn workspace_origin(self) -> String {
        format!("http://127.0.0.1:{}/chat/", self.chat)
    }
}

fn reserve_private_endpoint() -> std::io::Result<TcpListener> {
    // A host may customize its ephemeral range to include the CLI ports.
    for _ in 0..8 {
        let listener = TcpListener::bind(("127.0.0.1", 0))?;
        if ![8766, 8767].contains(&listener.local_addr()?.port()) {
            return Ok(listener);
        }
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::AddrInUse,
        "could not reserve a private LoopX endpoint outside the CLI ports",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vite_keeps_its_proxy_endpoints() {
        let endpoints = ServiceEndpoints::allocate(false).unwrap();
        assert_eq!(endpoints.port(ServiceKind::Status), 8766);
        assert_eq!(endpoints.port(ServiceKind::Chat), 8767);
        assert!(!endpoints.isolated);
    }

    #[test]
    fn packaged_windows_do_not_use_shared_or_duplicate_endpoints() {
        let endpoints = ServiceEndpoints::allocate(true).unwrap();
        assert!(endpoints.isolated);
        assert_ne!(endpoints.status, endpoints.chat);
        assert!(![8766, 8767].contains(&endpoints.status));
        assert!(![8766, 8767].contains(&endpoints.chat));
        assert_eq!(
            endpoints.workspace_origin(),
            format!("http://127.0.0.1:{}/chat/", endpoints.chat)
        );
    }
}
