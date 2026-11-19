use std::{ffi::OsStr, net::TcpListener};

use crate::services::ServiceKind;

/// A native window keeps one endpoint pair across runtime repairs. Normal
/// windows share the machine services; explicit private endpoints are opt-in.
#[derive(Clone, Copy)]
pub(crate) struct ServiceEndpoints {
    status: u16,
    chat: u16,
    pub isolated: bool,
}

impl ServiceEndpoints {
    pub fn for_window(private_endpoints: Option<&OsStr>) -> std::io::Result<Self> {
        let isolated = match private_endpoints {
            None => false,
            Some(value) if value == "0" => false,
            Some(value) if value == "1" => true,
            _ => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "LOOPX_DESKTOP_PRIVATE_ENDPOINTS must be 0 or 1",
                ))
            }
        };
        Self::allocate(isolated)
    }

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

    #[cfg(all(test, unix))]
    pub(crate) fn allocate_recovery_fixture() -> std::io::Result<Self> {
        // Keep the recovery fixtures outside the default ephemeral port range.
        // A parallel test's bind-to-zero must not take the port between this
        // reservation and the simulated Core child binding it.
        let start = 20_000 + (std::process::id() % 128) as u16 * 32;
        let mut available =
            (start..start + 32).filter_map(|port| TcpListener::bind(("127.0.0.1", port)).ok());
        let mut reserve = || {
            available.next().ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::AddrInUse,
                    "no free loopback port for the service recovery fixture",
                )
            })
        };
        let status = reserve()?;
        let chat = reserve()?;
        Ok(Self {
            status: status.local_addr()?.port(),
            chat: chat.local_addr()?.port(),
            isolated: true,
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
    fn ordinary_window_uses_the_same_machine_service_addresses() {
        for choice in [None, Some(OsStr::new("0"))] {
            let endpoints = ServiceEndpoints::for_window(choice).unwrap();
            assert_eq!(endpoints.port(ServiceKind::Status), 8766);
            assert_eq!(endpoints.port(ServiceKind::Chat), 8767);
            assert!(!endpoints.isolated);
        }
        assert!(
            ServiceEndpoints::for_window(Some(OsStr::new("1")))
                .unwrap()
                .isolated
        );
    }

    #[test]
    fn invalid_private_endpoint_choice_cannot_fall_through_to_machine_state() {
        for choice in ["", "true", "yes", "2"] {
            assert_eq!(
                ServiceEndpoints::for_window(Some(OsStr::new(choice)))
                    .err()
                    .unwrap()
                    .kind(),
                std::io::ErrorKind::InvalidInput
            );
        }
    }

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
