document.addEventListener('DOMContentLoaded', async () => {
    const token = localStorage.getItem('jwt_token');
    const authStatusDiv = document.getElementById('auth-status');
    const dashboardContent = document.getElementById('dashboard-content');

    // A URL base da sua API de perícia
    const API_BASE_URL = 'https://blublu-pericia-api.onrender.com/api';

    if (!token) {
        authStatusDiv.innerHTML = '<h1>Acesso Negado</h1><p>Você não está logado. Redirecionando para a página de login...</p>';
        setTimeout(() => {
            window.location.href = '/per'; // Redireciona para a página de login/ferramenta
        }, 3000);
        return;
    }

    try {
        // Para validar o token, tentamos acessar um endpoint protegido.
        // A rota GET /processes é ideal para isso.
        const response = await fetch(`${API_BASE_URL}/processes`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            }
        });

        if (response.ok) {
            // Se a resposta for OK (2xx), o token é válido.
            console.log('Autenticação bem-sucedida. Exibindo dashboard.');
            authStatusDiv.style.display = 'none'; // Esconde a mensagem de verificação
            dashboardContent.style.display = 'block'; // Mostra o conteúdo do dashboard
        } else {
            // Se a resposta for 401/403, o token é inválido ou expirou.
            throw new Error('Token inválido ou expirado.');
        }

    } catch (error) {
        console.error('Falha na verificação de autenticação:', error);
        localStorage.removeItem('jwt_token'); // Limpa o token inválido
        authStatusDiv.innerHTML = '<h1>Acesso Negado</h1><p>Sua sessão expirou ou é inválida. Redirecionando para a página de login...</p>';
        setTimeout(() => {
            window.location.href = '/per';
        }, 3000);
    }
});
