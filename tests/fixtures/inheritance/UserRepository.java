package com.example.inheritance;

public class UserRepository implements Repository<User> {

    public User findById(String id) {
        return new User(id, "test");
    }

    public void save(User entity) {
        System.out.println("Saving: " + entity.getName());
    }

    public void delete(String id) {
        System.out.println("Deleting: " + id);
    }
}
