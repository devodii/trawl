use soroban_sdk::{contract, contractimpl, contracttype, Address, Env};

macro_rules! guard { ($e:expr) => { if !$e { panic!("no") } }; }

#[contracttype]
#[derive(Clone, Debug)]
pub enum DataKey { Admin, Balance(Address) }

#[contracterror]
#[derive(Copy, Clone)]
pub enum Error { NotInitialized = 1, Unauthorized = 2 }

#[contract]
pub struct Token;

#[contractimpl]
impl Token {
    pub fn initialize(env: Env, admin: Address) {
        if env.storage().instance().has(&DataKey::Admin) { panic!("already"); }
        env.storage().instance().set(&DataKey::Admin, &admin);
    }

    // a comment about require_auth()
    pub fn set_fee(env: Env, #[auth] who: Address, fee: u32) {
        let admin: Address = env.storage().instance().get(&DataKey::Admin).unwrap();
        admin.require_auth();
        env.storage().persistent().extend_ttl(&DataKey::Admin, 1, 2);
    }
}

#[cfg(test)]
mod test {
    use super::*;
    fn setup() -> Env { let env = Env::default(); env.mock_all_auths(); env }
    #[test]
    fn works() { let env = setup(); }
}
